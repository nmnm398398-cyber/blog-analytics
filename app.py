import streamlit as st
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Metric, Dimension
)
from datetime import datetime
import json
import pytz
import pandas as pd
import urllib.parse
import re

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

st.title("📊 ブログ分析ダッシュボード All-in-One")

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
# 2. ブログ設定 (正しいURLを反映しました)
# ---------------------------------------------------------
BLOGS = [
    {"name": "🚙 ジムニーフリーク！", "id": "470121869", "url": "jimm.hateblo.jp"}, 
    {"name": "🎣 ソルトルアーのすすめ！", "id": "343862616", "url": "sbs614.hateblo.jp"},
    {"name": "👔 公務員転職マン", "id": "445135719", "url": "tdf.hatenablog.com"},
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

# ② 日別推移グラフ
def get_daily_trend_comparison(property_id, days):
    current_start = f"{days}daysAgo"
    current_end = "today"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    req_curr = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="screenPageViews")],
        order_bys=[{"dimension": {"dimension_name": "date"}}]
    )
    res_curr = client.run_report(req_curr)
    
    req_prev = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="screenPageViews")],
        order_bys=[{"dimension": {"dimension_name": "date"}}]
    )
    res_prev = client.run_report(req_prev)

    curr_data = [int(row.metric_values[0].value) for row in res_curr.rows] if res_curr.rows else []
    prev_data = [int(row.metric_values[0].value) for row in res_prev.rows] if res_prev.rows else []

    min_len = min(len(curr_data), len(prev_data))
    if min_len == 0: return pd.DataFrame(), sum(curr_data), sum(prev_data)

    df = pd.DataFrame({
        "今期のPV推移": curr_data[:min_len],
        "前期のPV推移": prev_data[:min_len]
    })
    
    return df, sum(curr_data), sum(prev_data)

# ③ 記事ランキング (エラー回避版)
def get_article_ranking_comparison(property_id, days):
    current_start = f"{days}daysAgo"
    current_end = "today"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    is_keyword_available = True
    raw_data = []

    try:
        req_curr = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
            dimensions=[Dimension(name="pageTitle"), Dimension(name="organicGoogleSearchQuery")],
            metrics=[Metric(name="screenPageViews"), Metric(name="organicGoogleSearchAveragePosition")],
            limit=2000
        )
        res_curr = client.run_report(req_curr)
        valid_kw = 0
        if res_curr.rows:
            for row in res_curr.rows:
                title = row.dimension_values[0].value
                kw = row.dimension_values[1].value
                pv = int(row.metric_values[0].value)
                rank = float(row.metric_values[1].value)
                clean_kw = ""
                if kw and kw not in ["(not set)", "(not provided)"]:
                    clean_kw = kw
                    valid_kw += 1
                if title and title != "(not set)":
                    raw_data.append({"title": title, "kw": clean_kw, "pv": pv, "rank": rank})
        if valid_kw == 0: raise ValueError("No keywords")

    except Exception:
        is_keyword_available = False
        raw_data = []
        req_fb = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
            dimensions=[Dimension(name="pageTitle"), Dimension(name="sessionSourceMedium")],
            metrics=[Metric(name="screenPageViews")],
            limit=1000
        )
        res_fb = client.run_report(req_fb)
        if res_fb.rows:
            for row in res_fb.rows:
                title = row.dimension_values[0].value
                info = row.dimension_values[1].value
                pv = int(row.metric_values[0].value)
                if title and title != "(not set)":
                    raw_data.append({"title": title, "kw": info, "pv": pv, "rank": 0})

    df_curr = pd.DataFrame(raw_data)
    if df_curr.empty: return pd.DataFrame()

    prev_rank_map = {}
    if is_keyword_available:
        try:
            req_prev = RunReportRequest(
                property=f"properties/{property_id}",
                date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
                dimensions=[Dimension(name="pageTitle"), Dimension(name="organicGoogleSearchQuery")],
                metrics=[Metric(name="organicGoogleSearchAveragePosition")],
                limit=2000
            )
            res_prev = client.run_report(req_prev)
            if res_prev.rows:
                for row in res_prev.rows:
                    t = row.dimension_values[0].value
                    k = row.dimension_values[1].value
                    r = float(row.metric_values[0].value)
                    prev_rank_map[(t, k)] = r
        except: pass

    req_prev_pv = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
        dimensions=[Dimension(name="pageTitle")],
        metrics=[Metric(name="screenPageViews")],
        limit=2000
    )
    res_prev_pv = client.run_report(req_prev_pv)
    prev_pv_map = {}
    if res_prev_pv.rows:
        for row in res_prev_pv.rows:
            prev_pv_map[row.dimension_values[0].value] = int(row.metric_values[0].value)

    df_grouped = df_curr.groupby("title")["pv"].sum().reset_index().rename(columns={"pv": "今期のPV"})
    df_grouped["前期のPV"] = df_grouped["title"].map(prev_pv_map).fillna(0).astype(int)
    df_grouped["差分"] = df_grouped["今期のPV"] - df_grouped["前期のPV"]
    
    def calc_pct(row):
        if row["前期のPV"] > 0: return f"{(row['差分'] / row['前期のPV'] * 100):+.1f}%"
        elif row["今期のPV"] > 0: return "NEW"
        else: return "0%"
    df_grouped["前期間比"] = df_grouped.apply(calc_pct, axis=1)

    def format_info(title):
        rows = df_curr[df_curr["title"] == title]
        if is_keyword_available:
            kws = rows[rows["kw"] != ""].sort_values("pv", ascending=False).head(3)
            if kws.empty: return "-"
            res = []
            for _, r in kws.iterrows():
                kw = r["kw"]
                cr = r["rank"]
                pr = prev_rank_map.get((title, kw), 0)
                rank_str = f"{cr:.1f}位"
                if pr > 0:
                    diff = pr - cr
                    if diff > 0: rank_str += f" (⬆{diff:.1f})"
                    elif diff < 0: rank_str += f" (⬇{abs(diff):.1f})"
                    else: rank_str += " (➡)"
                else: rank_str += " (NEW)"
                res.append(f"{kw}: {rank_str}")
            return " | ".join(res)
        else:
            sources = rows.groupby("kw")["pv"].sum().reset_index().sort_values("pv", ascending=False).head(3)
            return ", ".join(sources["kw"].tolist())

    col_name = "検索キーワード(TOP3)" if is_keyword_available else "主な流入元(TOP3)"
    df_grouped[col_name] = df_grouped["title"].apply(format_info)
    final = df_grouped.sort_values("今期のPV", ascending=False).head(30)
    final = final[["title", "今期のPV", "前期のPV", "前期間比", col_name]].rename(columns={"title": "記事タイトル"})
    return final

# ④ SNS流入分析 (Pythonフィルタ版 - エラー回避)
def get_sns_traffic_safe(property_id, days=7):
    """
    GA4 API側でフィルタせず、全データを取得してからPython側でSNSを抽出する
    """
    start_date = f"{days}daysAgo"
    
    # 全流入元を取得
    request = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=start_date, end_date="today")],
        dimensions=[Dimension(name="sessionSource"), Dimension(name="pageTitle")],
        metrics=[Metric(name="screenPageViews")],
        limit=5000
    )
    response = client.run_report(request)
    
    data = []
    # SNS判定用正規表現
    sns_pattern = re.compile(r"t\.co|twitter|facebook|instagram|linkedin|pinterest|youtube|threads", re.IGNORECASE)
    
    if response.rows:
        for row in response.rows:
            source = row.dimension_values[0].value
            title = row.dimension_values[1].value
            pv = int(row.metric_values[0].value)
            
            # Python側で判定
            if sns_pattern.search(source):
                # 表示名の整形
                label = source
                if "t.co" in source or "twitter" in source: label = "X (Twitter)"
                elif "facebook" in source: label = "Facebook"
                elif "instagram" in source: label = "Instagram"
                elif "threads" in source: label = "Threads"
                
                data.append({"SNS": label, "記事タイトル": title, "PV": pv})
            
    return pd.DataFrame(data)

# ---------------------------------------------------------
# 4. 画面表示
# ---------------------------------------------------------
st.write(f"最終更新: {now.strftime('%Y-%m-%d %H:%M:%S')}")

tab1, tab2, tab3 = st.tabs(["⏱️ リアルタイムPV", "📈 期間分析レポート", "📱 SNSでの言及・流入"])

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
    st.markdown("### 📈 期間比較レポート")
    col_sel, _ = st.columns([1, 2])
    with col_sel:
        period_days = st.selectbox("分析期間", [7, 30], index=0, format_func=lambda x: f"過去 {x} 日間")
    
    for blog in BLOGS:
        with st.expander(f"📊 {blog['name']} の詳細分析", expanded=True):
            try:
                df_trend, curr_sum, prev_sum = get_daily_trend_comparison(blog["id"], period_days)
                diff_total = curr_sum - prev_sum
                pct_total = (diff_total / prev_sum * 100) if prev_sum > 0 else 0
                
                st.markdown(f"#### 📅 総PV: {curr_sum:,} ({pct_total:+.1f}%)")
                if not df_trend.empty:
                    st.line_chart(df_trend, color=["#FF4B4B", "#CCCCCC"]) 
                    st.caption("赤線: 今期 / グレー線: 前期")

                df_top = get_article_ranking_comparison(blog["id"], period_days)
                if not df_top.empty:
                    st.markdown("#### 🏆 記事別ランキング TOP30 (検索順位付き)")
                    st.dataframe(df_top, use_container_width=True, hide_index=True, height=600)
                else:
                    st.warning("データなし")
            except Exception as e:
                st.error(f"エラー: {e}")

with tab3:
    st.markdown("### 📱 SNS流入 & エゴサーチ")
    st.caption("過去7日間にSNS（X, FB, Insta等）から流入があった記事と、SNS上の反応を確認します。")
    
    for blog in BLOGS:
        with st.expander(f"💬 {blog['name']}", expanded=True):
            try:
                # 修正した safe 関数を使用
                df_sns = get_sns_traffic_safe(blog["id"], 7)
                if not df_sns.empty:
                    total_sns = df_sns["PV"].sum()
                    st.metric("SNS経由の総PV (過去7日)", f"{total_sns} PV")
                    chart_data = df_sns.groupby("SNS")["PV"].sum()
                    st.bar_chart(chart_data, color="#1DA1F2")
                    st.markdown("**📌 SNSで話題になった記事**")
                    st.dataframe(df_sns, use_container_width=True, hide_index=True)
                else:
                    st.info("SNSからの流入はまだありません。")
            except Exception as e:
                st.error(f"データ取得エラー: {e}")

            st.markdown("---")
            st.markdown("**🔎 実際の投稿を探す (エゴサーチ)**")
            search_query = urllib.parse.quote(blog.get("url", "")) 
            if search_query:
                c1, c2 = st.columns(2)
                with c1:
                    st.link_button("X(Twitter)の反応を見る", f"https://search.yahoo.co.jp/realtime/search?p={search_query}")
                with c2:
                    st.link_button("SNS全体をGoogle検索", f"https://www.google.com/search?q=site:x.com+{search_query}+OR+site:facebook.com+{search_query}")
            else:
                st.warning("設定(BLOGS)にurlを入れてください")
