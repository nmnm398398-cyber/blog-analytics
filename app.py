import streamlit as st
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Metric, Dimension
)
from datetime import datetime
import json
import pytz
import pandas as pd

# ---------------------------------------------------------
# 0. ページ設定 & パスワード認証 (最優先で実行)
# ---------------------------------------------------------
st.set_page_config(page_title="Blog Analytics Pro", layout="wide")

def check_password():
    """パスワード認証機能"""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        # ログイン画面の表示
        st.title("🔒 ログイン")
        password_input = st.text_input("パスワードを入力してください", type="password")
        
        if st.button("ログイン"):
            # Secretsに設定したパスワードと照合
            if password_input == st.secrets["auth"]["password"]:
                st.session_state.authenticated = True
                st.rerun() # 画面を再読み込みしてメイン処理へ
            else:
                st.error("パスワードが違います")
        
        # 認証されていない場合はここで処理を強制終了
        st.stop()

# 認証チェックを実行 (ここを通らないと下には進めない)
check_password()


# =========================================================
#  ここから下は認証成功後にしか実行されません
# =========================================================

st.title("📊 ブログ分析ダッシュボード Pro")

# 現在時刻
JST = pytz.timezone('Asia/Tokyo')
now = datetime.now(JST)
current_hour = now.hour

# ---------------------------------------------------------
# 1. 認証 (GCP)
# ---------------------------------------------------------
try:
    creds_json = json.loads(st.secrets["gcp_service_account"])
    client = BetaAnalyticsDataClient.from_service_account_info(creds_json)
except Exception as e:
    st.error(f"GCP認証エラー: {e}")
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
# 3. データ取得ロジック
# ---------------------------------------------------------

def get_realtime_metrics(property_id):
    req_today = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="today", end_date="today")],
        metrics=[Metric(name="screenPageViews")],
    )
    res_today = client.run_report(req_today)
    pv_today = int(res_today.rows[0].metric_values[0].value) if res_today.rows else 0

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

def get_top_pages_with_keywords(property_id, days):
    start_date = f"{days}daysAgo"
    try:
        request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=start_date, end_date="today")],
            dimensions=[Dimension(name="pageTitle"), Dimension(name="organicGoogleSearchQuery")],
            metrics=[Metric(name="screenPageViews")],
            limit=1000
        )
        response = client.run_report(request)
        
        raw_data = []
        if response.rows:
            for row in response.rows:
                title = row.dimension_values[0].value
                kw = row.dimension_values[1].value
                pv = int(row.metric_values[0].value)
                if title and title != "(not set)":
                    clean_kw = kw if kw and kw not in ["(not set)", "(not provided)"] else ""
                    raw_data.append({"title": title, "kw": clean_kw, "pv": pv})
        
        df_raw = pd.DataFrame(raw_data)
        if df_raw.empty: return pd.DataFrame()

        pv_sum = df_raw.groupby("title")["pv"].sum().reset_index().sort_values("pv", ascending=False)
        kw_data = df_raw[df_raw["kw"] != ""].sort_values("pv", ascending=False)
        
        def get_top_kws(title):
            kws = kw_data[kw_data["title"] == title]["kw"].head(3).tolist()
            return ", ".join(kws) if kws else "データなし"

        pv_sum["流入キーワード(TOP3)"] = pv_sum["title"].apply(get_top_kws)
        final_df = pv_sum.head(30).rename(columns={"title": "記事タイトル", "pv": "PV数"})
        return final_df

    except Exception:
        # エラー時のフォールバック
        request_fb = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=start_date, end_date="today")],
            dimensions=[Dimension(name="pageTitle")],
            metrics=[Metric(name="screenPageViews")],
            limit=30
        )
        resp_fb = client.run_report(request_fb)
        data_fb = []
        if resp_fb.rows:
            for row in resp_fb.rows:
                title = row.dimension_values[0].value
                pv = int(row.metric_values[0].value)
                if title and title != "(not set)":
                    data_fb.append({"記事タイトル": title, "PV数": pv, "流入キーワード(TOP3)": "-"})
        return pd.DataFrame(data_fb)

# ---------------------------------------------------------
# 4. 画面表示
# ---------------------------------------------------------
st.write(f"最終更新: {now.strftime('%Y-%m-%d %H:%M:%S')}")

tab1, tab2 = st.tabs(["⏱️ リアルタイムPV", "🏆 記事ランキングTOP30"])

with tab1:
    cols = st.columns(3)
    for i, blog in enumerate(BLOGS):
        with cols[i]:
            st.subheader(blog["name"])
            try:
                today, yest_same, yest_total = get_realtime_metrics(blog["id"])
                diff = today - yest_same
                pct = (diff / yest_same * 100) if yest_same > 0 else 0
                st.metric("今日のPV", f"{today:,}", f"{diff:+,} ({pct:+.1f}%)")
                st.caption(f"昨日同時刻: {yest_same:,} / 昨日計: {yest_total:,}")
            except Exception:
                st.error("取得エラー")

    if st.button("更新", key="refresh_realtime"):
        st.rerun()

with tab2:
    st.markdown("### 🏆 人気記事 TOP30 & 流入キーワード")
    period_days = st.selectbox("集計期間を選択", [7, 30], index=1, format_func=lambda x: f"過去 {x} 日間")
    
    for blog in BLOGS:
        with st.expander(f"📊 {blog['name']} のランキングを見る", expanded=False):
            try:
                df_top = get_top_pages_with_keywords(blog["id"], period_days)
                if not df_top.empty:
                    st.markdown("#### 📈 PV数比較 (TOP30)")
                    chart_df = df_top.set_index("記事タイトル")[["PV数"]].sort_values("PV数", ascending=True)
                    st.bar_chart(chart_df, horizontal=True, color="#FF4B4B")
                    
                    st.markdown("#### 📝 詳細データ")
                    st.dataframe(
                        df_top[["記事タイトル", "PV数", "流入キーワード(TOP3)"]], 
                        use_container_width=True, hide_index=True, height=500
                    )
                else:
                    st.warning("データなし")
            except Exception as e:
                st.error(f"エラー: {e}")
