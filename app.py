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
# 0. ページ設定 & パスワード認証
# ---------------------------------------------------------
st.set_page_config(page_title="Blog Analytics Ultimate", layout="wide")

def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.title("🔒 ログイン")
        password_input = st.text_input("パスワードを入力してください", type="password")
        if st.button("ログイン"):
            if password_input == st.secrets["auth"]["password"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("パスワードが違います")
        st.stop()

check_password()

# =========================================================
#  メイン処理
# =========================================================

st.title("📊 ブログ分析ダッシュボード Ultimate")

JST = pytz.timezone('Asia/Tokyo')
now = datetime.now(JST)
current_hour = now.hour

# ---------------------------------------------------------
# 1. 認証
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

# ① リアルタイムPV
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

# ② 日別推移グラフ用データ (今期 vs 前期)
def get_daily_trend_comparison(property_id, days):
    """
    今期と前期の日別PVを取得し、重ねて表示できるDataFrameを作成する
    """
    current_start = f"{days}daysAgo"
    current_end = "today"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    # 今期データ
    req_curr = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
        dimensions=[Dimension(name="date")], # 日付
        metrics=[Metric(name="screenPageViews")],
        order_bys=[{"dimension": {"dimension_name": "date"}}]
    )
    res_curr = client.run_report(req_curr)
    
    # 前期データ
    req_prev = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="screenPageViews")],
        order_bys=[{"dimension": {"dimension_name": "date"}}]
    )
    res_prev = client.run_report(req_prev)

    # データをリスト化 (日付そのものではなく「N日目」で合わせる)
    curr_data = []
    if res_curr.rows:
        for row in res_curr.rows:
            curr_data.append(int(row.metric_values[0].value))

    prev_data = []
    if res_prev.rows:
        for row in res_prev.rows:
            prev_data.append(int(row.metric_values[0].value))

    # 長さを揃えてDataFrame化
    # (APIの仕様上、今日を含めると長さがズレることがあるので短い方に合わせる等の処理)
    min_len = min(len(curr_data), len(prev_data))
    if min_len == 0: return pd.DataFrame()

    df = pd.DataFrame({
        "今期のPV推移": curr_data[:min_len],
        "前期のPV推移": prev_data[:min_len]
    })
    
    return df, sum(curr_data), sum(prev_data)

# ③ 記事ランキング比較 (差分％対応)
def get_article_ranking_comparison(property_id, days):
    current_start = f"{days}daysAgo"
    current_end = "today"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    # --- A. 今期のデータ ---
    try:
        req_curr = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
            dimensions=[Dimension(name="pageTitle"), Dimension(name="organicGoogleSearchQuery")],
            metrics=[Metric(name="screenPageViews")],
            limit=1000
        )
        res_curr = client.run_report(req_curr)
        raw_data = []
        is_keyword = True
        if res_curr.rows:
            for row in res_curr.rows:
                title = row.dimension_values[0].value
                info = row.dimension_values[1].value
                pv = int(row.metric_values[0].value)
                if title and title != "(not set)":
                    clean = info if info and info not in ["(not set)", "(not provided)"] else ""
                    raw_data.append({"title": title, "info": clean, "pv": pv})

    except Exception:
        is_keyword = False
        req_fb = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
            dimensions=[Dimension(name="pageTitle"), Dimension(name="sessionSourceMedium")],
            metrics=[Metric(name="screenPageViews")],
            limit=1000
        )
        res_fb = client.run_report(req_fb)
        raw_data = []
        if res_fb.rows:
            for row in res_fb.rows:
                title = row.dimension_values[0].value
                info = row.dimension_values[1].value
                pv = int(row.metric_values[0].value)
                if title and title != "(not set)":
                    raw_data.append({"title": title, "info": info, "pv": pv})

    df_curr = pd.DataFrame(raw_data)
    if df_curr.empty: return pd.DataFrame()

    df_curr_grouped = df_curr.groupby("title")["pv"].sum().reset_index().rename(columns={"pv": "今期のPV"})
    
    info_data = df_curr[df_curr["info"] != ""].sort_values("pv", ascending=False)
    def get_top_infos(title):
        infos = info_data[info_data["title"] == title]["info"].head(3).tolist()
        return ", ".join(infos) if infos else "-"
    
    col_info = "検索キーワード(TOP3)" if is_keyword else "主な流入元(TOP3)"
    df_curr_grouped[col_info] = df_curr_grouped["title"].apply(get_top_infos)

    # --- B. 前期のデータ ---
    req_prev = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
        dimensions=[Dimension(name="pageTitle")],
        metrics=[Metric(name="screenPageViews")],
        limit=2000
    )
    res_prev = client.run_report(req_prev)
    prev_data = []
    if res_prev.rows:
        for row in res_prev.rows:
            prev_data.append({
                "title": row.dimension_values[0].value,
                "前期のPV": int(row.metric_values[0].value)
            })
    
    df_prev = pd.DataFrame(prev_data)
    
    # --- C. 結合と計算 ---
    merged = pd.merge(df_curr_grouped, df_prev, on="title", how="left")
    merged["前期のPV"] = merged["前期のPV"].fillna(0).astype(int)
    
    # 差分とパーセンテージ計算
    merged["差分"] = merged["今期のPV"] - merged["前期のPV"]
    
    def calc_pct(row):
        if row["前期のPV"] > 0:
            return f"{(row['差分'] / row['前期のPV'] * 100):+.1f}%"
        elif row["今期のPV"] > 0:
            return "NEW" # 前期0で今期ありの場合
        else:
            return "0%"

    merged["前期間比"] = merged.apply(calc_pct, axis=1)

    # ソートと列整理
    final = merged.sort_values("今期のPV", ascending=False).head(30)
    final = final[["title", "今期のPV", "前期のPV", "前期間比", col_info]]
    final = final.rename(columns={"title": "記事タイトル"})
    
    return final

# ---------------------------------------------------------
# 4. 画面表示
# ---------------------------------------------------------
st.write(f"最終更新: {now.strftime('%Y-%m-%d %H:%M:%S')}")

tab1, tab2 = st.tabs(["⏱️ リアルタイムPV", "📈 期間分析レポート"])

# --- タブ1 ---
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

# --- タブ2 ---
with tab2:
    st.markdown("### 📈 期間比較レポート")
    
    col_sel, _ = st.columns([1, 2])
    with col_sel:
        period_days = st.selectbox(
            "分析期間", [7, 30], index=1, 
            format_func=lambda x: f"過去 {x} 日間 vs その前の {x} 日間"
        )
    
    for blog in BLOGS:
        with st.expander(f"📊 {blog['name']} の詳細分析", expanded=True):
            try:
                # 1. 折れ線グラフ (日別推移比較)
                df_trend, curr_sum, prev_sum = get_daily_trend_comparison(blog["id"], period_days)
                
                # サマリー表示
                diff_total = curr_sum - prev_sum
                pct_total = (diff_total / prev_sum * 100) if prev_sum > 0 else 0
                
                st.markdown(f"#### 📅 総PV: {curr_sum:,} ({pct_total:+.1f}%)")
                
                if not df_trend.empty:
                    # 折れ線グラフの表示
                    st.line_chart(df_trend, color=["#FF4B4B", "#CCCCCC"]) 
                    # ※赤色が今期、グレーが前期になるように設定
                    st.caption("赤線: 今期の推移 / グレー線: 前期の推移")

                # 2. ランキング表
                df_top = get_article_ranking_comparison(blog["id"], period_days)
                
                if not df_top.empty:
                    st.markdown("#### 🏆 記事別ランキング TOP30")
                    st.dataframe(
                        df_top, 
                        use_container_width=True, 
                        hide_index=True,
                        height=600
                    )
                else:
                    st.warning("データなし")
                    
            except Exception as e:
                st.error(f"エラー: {e}")
