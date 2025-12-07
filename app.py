import streamlit as st
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Metric, Dimension, FilterExpression, Filter, StringFilter
)
from datetime import datetime, timedelta
import json
import pytz
import pandas as pd

# ページ設定
st.set_page_config(page_title="Blog Analytics", layout="wide")
st.title("📊 ブログ分析ダッシュボード")

# 現在時刻（日本時間）
JST = pytz.timezone('Asia/Tokyo')
now = datetime.now(JST)
current_hour = now.hour

# ---------------------------------------------------------
# 1. 認証 (Streamlit CloudのSecretsを使う)
# ---------------------------------------------------------
try:
    creds_json = json.loads(st.secrets["gcp_service_account"])
    client = BetaAnalyticsDataClient.from_service_account_info(creds_json)
except Exception as e:
    st.error(f"認証エラー: Secretsの設定を確認してください。\n{e}")
    st.stop()

# ---------------------------------------------------------
# 2. ブログ設定
# ---------------------------------------------------------
BLOGS = [
    {"name": "🚙 ジムニーフリーク！", "id": "470121869"},
    {"name": "🎣 ソルトルアーのすすめ！", "id": "343862616"},
    {"name": "👔 公務員転職マン", "id": "445135719"},
]

# ---------------------------------------------------------
# 3. データ取得関数群
# ---------------------------------------------------------

# ① リアルタイムPV取得（既存機能）
def get_realtime_metrics(property_id):
    # A. 今日の累計
    req_today = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="today", end_date="today")],
        metrics=[Metric(name="screenPageViews")],
    )
    res_today = client.run_report(req_today)
    pv_today = int(res_today.rows[0].metric_values[0].value) if res_today.rows else 0

    # B. 昨日の同時刻データ
    req_yest = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="yesterday", end_date="yesterday")],
        dimensions=[Dimension(name="hour")],
        metrics=[Metric(name="screenPageViews")],
    )
    res_yest = client.run_report(req_yest)
    
    pv_yest_same = 0
    pv_yest_total = 0
    if res_yest.rows:
        for row in res_yest.rows:
            h = int(row.dimension_values[0].value)
            pv = int(row.metric_values[0].value)
            pv_yest_total += pv
            if h <= current_hour:
                pv_yest_same += pv
                
    return pv_today, pv_yest_same, pv_yest_total

# ② 検索ワードランキング取得（新機能）
def get_search_keywords(property_id, days):
    """
    指定期間の検索キーワードTOP100を取得
    ※ searchConsoleがリンクされていないと空になる可能性があります
    """
    start_date = f"{days}daysAgo"
    
    request = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=start_date, end_date="today")],
        dimensions=[Dimension(name="organicGoogleSearchQuery")], # 検索クエリ
        metrics=[Metric(name="screenPageViews")],
        limit=100
    )
    response = client.run_report(request)
    
    data = []
    if response.rows:
        for row in response.rows:
            word = row.dimension_values[0].value
            pv = int(row.metric_values[0].value)
            # 空文字や(not provided)を除外して見やすくする
            if word and word != "(not set)" and word != "(not provided)":
                data.append({"キーワード": word, "PV": pv})
    
    return pd.DataFrame(data)

# ③ 検索流入の推移グラフ用データ（新機能）
def get_organic_trend(property_id):
    """
    過去30日間の「検索流入(Organic)」の日別推移を取得
    """
    request = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="30daysAgo", end_date="today")],
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="screenPageViews")],
        # Organic検索のみにフィルタリング
        filter=FilterExpression(
            filter=Filter(
                field_name="sessionDefaultChannelGroup",
                string_filter=StringFilter(value="Organic Search")
            )
        ),
        order_bys=[{"dimension": {"dimension_name": "date"}}]
    )
    response = client.run_report(request)
    
    data = []
    if response.rows:
        for row in response.rows:
            date_str = row.dimension_values[0].value # YYYYMMDD形式
            pv = int(row.metric_values[0].value)
            # 日付をdatetime型に変換
            dt = datetime.strptime(date_str, "%Y%m%d")
            data.append({"日付": dt, "検索流入PV": pv})
            
    df = pd.DataFrame(data)
    if not df.empty:
        df = df.set_index("日付")
    return df

# ---------------------------------------------------------
# 4. 画面表示 (タブ構成)
# ---------------------------------------------------------
st.write(f"最終更新: {now.strftime('%Y-%m-%d %H:%M:%S')}")

# タブの作成
tab1, tab2 = st.tabs(["⏱️ リアルタイムPV", "🔍 検索ワード分析"])

# --- タブ1：リアルタイムPV ---
with tab1:
    cols = st.columns(3)
    for i, blog in enumerate(BLOGS):
        with cols[i]:
            st.subheader(blog["name"])
            try:
                today, yest_same, yest_total = get_realtime_metrics(blog["id"])
                diff = today - yest_same
                pct = (diff / yest_same * 100) if yest_same > 0 else 0
                
                st.metric(
                    "今日のPV", 
                    f"{today:,}", 
                    f"{diff:+,} ({pct:+.1f}%)"
                )
                st.caption(f"昨日同時刻: {yest_same:,} / 昨日計: {yest_total:,}")
            except Exception as e:
                st.error("取得エラー")

    if st.button("更新", key="refresh_realtime"):
        st.rerun()

# --- タブ2：検索ワード分析 ---
with tab2:
    st.markdown("### 🔍 検索流入レポート (Google検索)")
    st.caption("※このデータが表示されない場合、GA4とSearch Consoleの連携設定を確認してください。")

    # ブログごとにセクションを分ける
    for blog in BLOGS:
        with st.expander(f"📊 {blog['name']} の分析を見る", expanded=False):
            
            # 1. 検索流入の推移グラフ (過去30日)
            st.markdown("#### 📅 過去30日の検索流入推移")
            try:
                trend_df = get_organic_trend(blog["id"])
                if not trend_df.empty:
                    st.line_chart(trend_df, color="#FF4B4B") # ストリームリットカラー
                else:
                    st.info("データがありません")
            except Exception as e:
                st.error(f"グラフ取得エラー: {e}")

            # 2. キーワードランキング (7日 vs 30日)
            st.markdown("#### 🔑 流入キーワード TOP100")
            
            col_left, col_right = st.columns(2)
            
            with col_left:
                st.markdown("**過去 1週間 (7日間)**")
                try:
                    df_7 = get_search_keywords(blog["id"], 7)
                    if not df_7.empty:
                        st.dataframe(df_7, height=400, use_container_width=True)
                    else:
                        st.warning("データなし (または(not set)のみ)")
                except Exception as e:
                    st.error(f"エラー: {e}")

            with col_right:
                st.markdown("**過去 1ヶ月 (30日間)**")
                try:
                    df_30 = get_search_keywords(blog["id"], 30)
                    if not df_30.empty:
                        st.dataframe(df_30, height=400, use_container_width=True)
                    else:
                        st.warning("データなし")
                except Exception as e:
                    st.error(f"エラー: {e}")
