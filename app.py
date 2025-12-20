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
st.set_page_config(page_title="Blog Analytics Pro", layout="wide")

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

st.title("📊 ブログ分析ダッシュボード (Keyword Focus)")

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
    {"name": "🚙 ジムニーフリーク！", "id": "470121869", "url": "jimm.hateblo.jp"}, 
    {"name": "🎣 ソルトルアーのすすめ！", "id": "343862616", "url": "sbs614.hateblo.jp"},
    {"name": "👔 公務員転職マン", "id": "445135719", "url": "tdf.hatenablog.com"},
]

# ---------------------------------------------------------
# 3. データ取得ロジック
# ---------------------------------------------------------

# ① リアルタイムPV
def get_realtime_metrics(property_id):
    try:
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
    except Exception:
        return 0, 0, 0

# ② 日別推移グラフ
def get_daily_trend_comparison(property_id, days):
    current_start = f"{days}daysAgo"
    current_end = "today"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    try:
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
    except Exception:
        return pd.DataFrame(), 0, 0

# ③ 記事ランキング (キーワード・流入元 混合取得ロジック)
def get_article_ranking_comparison(property_id, days):
    current_start = f"{days}daysAgo"
    current_end = "today"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    raw_data = []

    # キーワード取得に挑戦
    try:
        req_curr = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
            dimensions=[
                Dimension(name="pageTitle"), 
                Dimension(name="organicGoogleSearchQuery"), # キーワード
                Dimension(name="sessionSourceMedium")       # 流入元
            ],
            metrics=[Metric(name="screenPageViews"), Metric(name="organicGoogleSearchAveragePosition")],
            limit=3000
        )
        res_curr = client.run_report(req_curr)
        
        if res_curr.rows:
            for row in res_curr.rows:
                title = row.dimension_values[0].value
                kw = row.dimension_values[1].value
                source = row.dimension_values[2].value
                pv = int(row.metric_values[0].value)
                rank = float(row.metric_values[1].value)

                # 表示データの優先順位決定
                # 1. キーワードがあるならそれを採用
                # 2. なければ流入元を採用
                display_info = ""
                is_valid_kw = False
                
                if kw and kw not in ["(not set)", "(not provided)", ""]:
                    display_info = kw
                    is_valid_kw = True
                else:
                    display_info = f"[{source}]"
                    rank = 0

                if title and title != "(not set)":
                    raw_data.append({
                        "title": title, 
                        "info": display_info, 
                        "pv": pv, 
                        "rank": rank,
                        "is_kw": is_valid_kw
                    })

    except Exception as e:
        # 権限不足などでキーワードが取れない場合のハンドリング
        err_msg = str(e)
        if "organicGoogleSearchQuery is not a valid dimension" in err_msg:
            st.error(f"⚠️ **権限エラー (ID: {property_id})**")
            st.error("Google Search Consoleのデータを取得できませんでした。")
            st.info("※ ロボットのメールアドレスをSearch Consoleの「ユーザーと権限」に追加しましたか？")
        return pd.DataFrame()

    df_curr = pd.DataFrame(raw_data)
    if df_curr.empty: return pd.DataFrame()

    # 前期データ (比較用)
    prev_rank_map = {}
    try:
        req_prev = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
            dimensions=[Dimension(name="pageTitle"), Dimension(name="organicGoogleSearchQuery")],
            metrics=[Metric(name="organicGoogleSearchAveragePosition")],
            limit=3000
        )
        res_prev = client.run_report(req_prev)
        if res_prev.rows:
            for row in res_prev.rows:
                t = row.dimension_values[0].value
                k = row.dimension_values[1].value
                r = float(row.metric_values[0].value)
                prev_rank_map[(t, k)] = r
    except: pass

    # 前期PV
    prev_pv_map = {}
    try:
        req_prev_pv = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
            dimensions=[Dimension(name="pageTitle")],
            metrics=[Metric(name="screenPageViews")],
            limit=3000
        )
        res_prev_pv = client.run_report(req_prev_pv)
        if res_prev_pv.rows:
            for row in res_prev_pv.rows:
                prev_pv_map[row.dimension_values[0].value] = int(row.metric_values[0].value)
    except: pass

    # 集計処理
    df_grouped = df_curr.groupby("title")["pv"].sum().reset_index().rename(columns={"pv": "今期のPV"})
    df_grouped["前期のPV"] = df_grouped["title"].map(prev_pv_map).fillna(0).astype(int)
    
    df_grouped["差分"] = df_grouped["今期のPV"] - df_grouped["前期のPV"]
    def calc_pct(row):
        if row["前期のPV"] > 0: return f"{(row['差分'] / row['前期のPV'] * 100):+.1f}%"
        elif row["今期のPV"] > 0: return "NEW"
        else: return "0%"
    df_grouped["前期間比"] = df_grouped.apply(calc_pct, axis=1)

    # 詳細カラムの生成 (キーワードと流入元の混在)
    def format_mixed_info(title):
        rows = df_curr[df_curr["title"] == title]
        # PVが多い順に上位3つを表示
        top_items = rows.groupby("info")[["pv", "rank", "is_kw"]].max().sort_values("pv", ascending=False).head(3)
        res = []
        for info, row in top_items.iterrows():
            if row["is_kw"]:
                kw = info
                cr = row["rank"]
                pr = prev_rank_map.get((title, kw), 0)
                rank_str = f"{cr:.1f}位"
                if pr > 0:
                    diff = pr - cr
                    if diff > 0: rank_str += f"(⬆{diff:.1f})"
                    elif diff < 0: rank_str += f"(⬇{abs(diff):.1f})"
                else: rank_str += "(NEW)"
                res.append(f"{kw}: {rank_str}")
            else:
                res.append(f"{info}")
        return " | ".join(res)

    df_grouped["検索キーワード / 流入元"] = df_grouped["title"].apply(format_mixed_info)
    final = df_grouped.sort_values("今期のPV", ascending=False).head(30)
    final = final[["title", "今期のPV", "前期のPV", "前期間比", "検索キーワード / 流入元"]].rename(columns={"title": "記事タイトル"})
    return final

# ④ SNS流入分析
def get_sns_traffic_safe(property_id, domain, days=7):
    start_date = f"{days}daysAgo"
    try:
        request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=start_date, end_date="today")],
            dimensions=[Dimension(name="sessionSource"), Dimension(name="pageTitle"), Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            limit=5000
        )
        response = client.run_report(request)
    except Exception:
        return pd.DataFrame()

    data = []
    sns_pattern = re.compile(r"t\.co|twitter|facebook|instagram|linkedin|pinterest|youtube|threads", re.IGNORECASE)
    
    if response.rows:
        for row in response.rows:
            source = row.dimension_values[0].value
            title = row.dimension_values[1].value
            path = row.dimension_values[2].value
            pv = int(row.metric_values[0].value)
            
            if sns_pattern.search(source):
                label = source
                if "t.co" in source or "twitter" in source: label = "X (Twitter)"
                elif "facebook" in source: label = "Facebook"
                elif "instagram" in source: label = "Instagram"
                elif "threads" in source: label = "Threads"
                
                full_url = f"{domain}{path}"
                search_url = f"https://search.yahoo.co.jp/realtime/search?p={urllib.parse.quote(full_url)}"
                
                data.append({"SNS": label, "記事タイトル": title, "PV": pv, "search_link": search_url})
            
    return pd.DataFrame(data)

# ⑤ 接続診断モード
def run_diagnostic(property_id):
    st.write("---")
    st.markdown(f"#### 🕵️ GA4 × SearchConsole 接続診断レポート (ID: `{property_id}`)")
    
    try:
        req = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date="30daysAgo", end_date="today")],
            dimensions=[Dimension(name="organicGoogleSearchQuery")],
            metrics=[Metric(name="screenPageViews")],
            limit=100
        )
        res = client.run_report(req)
        
        valid_kw_sample = []
        if res.rows:
            for row in res.rows:
                kw = row.dimension_values[0].value
                if kw not in ["(not set)", "(not provided)", ""]:
                    valid_kw_sample.append(kw)
        
        if len(valid_kw_sample) > 0:
            st.success(f"✅ **接続成功！** {len(valid_kw_sample)} 個の有効なキーワードが見つかりました。")
            st.markdown(f"**検出されたキーワード例:** `{', '.join(valid_kw_sample[:5])}`...")
        else:
            st.warning("⚠️ 接続はできていますが、有効なキーワードが0件です。（(not set)のみ）")
            st.info("プライバシー保護による除外の可能性があります。")
            
    except Exception as e:
        err_msg = str(e)
        st.error("❌ **接続エラーまたは設定無効**")
        if "not a valid dimension" in err_msg or "organicGoogleSearchQuery" in err_msg:
            st.error(f"""
            **【重要】権限設定が必要です**
            
            ロボット（`streamlit-user...`）が、Google Search Consoleのデータにアクセスできません。
            Search Consoleの管理画面で、このロボットを「ユーザー」として追加してください。
            """)
        else:
            st.error(f"APIエラー詳細: {e}")

# ---------------------------------------------------------
# 4. 画面表示
# ---------------------------------------------------------
st.write(f"最終更新: {now.strftime('%Y-%m-%d %H:%M:%S')}")

tab1, tab2, tab3, tab4 = st.tabs(["⏱️ リアルタイムPV", "📈 期間分析レポート", "📱 SNSでの言及・流入", "🛠️ 接続診断"])

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
                # 日別推移
                df_trend, curr_sum, prev_sum = get_daily_trend_comparison(blog["id"], period_days)
                diff_total = curr_sum - prev_sum
                pct_total = (diff_total / prev_sum * 100) if prev_sum > 0 else 0
                st.markdown(f"#### 📅 総PV: {curr_sum:,} ({pct_total:+.1f}%)")
                if not df_trend.empty:
                    st.line_chart(df_trend, color=["#FF4B4B", "#CCCCCC"]) 
                    st.caption("赤線: 今期 / グレー線: 前期")
                
                # ランキング
                df_top = get_article_ranking_comparison(blog["id"], period_days)
                if not df_top.empty:
                    st.markdown("#### 🏆 記事別ランキング TOP30")
                    st.dataframe(df_top, use_container_width=True, hide_index=True, height=600)
                else:
                    st.warning("データなし")
            except Exception as e:
                st.error(f"エラー: {e}")

with tab3:
    st.markdown("### 📱 SNS流入 & エゴサーチ")
    for blog in BLOGS:
        with st.expander(f"💬 {blog['name']}", expanded=True):
            try:
                df_sns = get_sns_traffic_safe(blog["id"], blog["url"], 7)
                if not df_sns.empty:
                    total_sns = df_sns["PV"].sum()
                    st.metric("SNS経由の総PV (過去7日)", f"{total_sns} PV")
                    st.bar_chart(df_sns.groupby("SNS")["PV"].sum(), color="#1DA1F2")
                    st.dataframe(
                        df_sns,
                        column_config={"search_link": st.column_config.LinkColumn("投稿を確認", display_text="検索 🔎")},
                        use_container_width=True, hide_index=True
                    )
                else:
                    st.info("SNS流入なし")
            except Exception as e:
                st.error(f"エラー: {e}")
            st.markdown("---")
            q = urllib.parse.quote(blog.get("url", "")) 
            if q:
                c1, c2 = st.columns(2)
                c1.link_button("X(Twitter)反応", f"https://search.yahoo.co.jp/realtime/search?p={q}")
                c2.link_button("SNS全体Google検索", f"https://www.google.com/search?q=site:x.com+{q}+OR+site:facebook.com+{q}")

with tab4:
    st.markdown("### 🛠️ 接続診断（デバッグモード）")
    st.write("検索キーワードが取得できているか、生のデータをチェックします。")
    selected_blog = st.selectbox("診断するブログを選択", [b["name"] for b in BLOGS])
    if st.button("診断開始"):
        target_id = next(b["id"] for b in BLOGS if b["name"] == selected_blog)
        run_diagnostic(target_id)
