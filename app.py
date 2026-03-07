import streamlit as st
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Metric, Dimension
)
from googleapiclient.discovery import build
from google.oauth2 import service_account
from datetime import datetime, timedelta
import json
import pytz
import pandas as pd
import urllib.parse
import re
import io

# =========================================================
#  0. ページ設定 & パスワード認証
# =========================================================
st.set_page_config(page_title="📊 ブログ分析ダッシュボード", layout="wide")

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

st.title("📊 ブログ分析ダッシュボード")

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
@st.cache_data(ttl=1800)
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

        curr_data = []
        curr_dates = [] 
        if res_curr.rows:
            for row in res_curr.rows:
                curr_data.append(int(row.metric_values[0].value))
                d = row.dimension_values[0].value  
                curr_dates.append(f"{d[4:6]}/{d[6:8]}") 

        prev_data = [int(row.metric_values[0].value) for row in res_prev.rows] if res_prev.rows else []

        min_len = min(len(curr_data), len(prev_data))
        if min_len == 0: return pd.DataFrame(), sum(curr_data), sum(prev_data)

        df = pd.DataFrame(index=curr_dates[:min_len])
        df["今期総PV"] = curr_data[:min_len]
        df["前期総PV"] = prev_data[:min_len]
        
        return df, sum(curr_data), sum(prev_data)
    except Exception:
        return pd.DataFrame(), 0, 0

# Search Consoleから検索キーワードを取得（全デバイス）
@st.cache_data(ttl=3600)
def get_search_console_keywords(site_url, days):
    try:
        creds_json = json.loads(st.secrets["gcp_service_account"])
        sc_creds = service_account.Credentials.from_service_account_info(
            creds_json, 
            scopes=['https://www.googleapis.com/auth/webmasters.readonly']
        )
        sc_service = build('searchconsole', 'v1', credentials=sc_creds)
        
        end_date = (datetime.now(JST) - timedelta(days=1)).strftime('%Y-%m-%d')
        start_date = (datetime.now(JST) - timedelta(days=days+1)).strftime('%Y-%m-%d')
        
        request = {
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': ['page', 'query'],
            'rowLimit': 10000
        }
        
        property_uri = f"sc-domain:{site_url}"
        try:
            response = sc_service.searchanalytics().query(siteUrl=property_uri, body=request).execute()
        except Exception:
            property_uri = f"https://{site_url}/"
            response = sc_service.searchanalytics().query(siteUrl=property_uri, body=request).execute()
            
        rows = response.get('rows', [])
        
        kw_map = {}
        for row in rows:
            page_url = row['keys'][0]
            query = row['keys'][1]
            clicks = row['clicks']
            
            if clicks > 0:
                if page_url not in kw_map:
                    kw_map[page_url] = []
                kw_map[page_url].append({"query": query, "clicks": clicks})
                
        final_map = {}
        for page_url, kws in kw_map.items():
            path = urllib.parse.urlparse(page_url).path
            kws_sorted = sorted(kws, key=lambda x: x['clicks'], reverse=True)
            top_kws = kws_sorted[:5]
            final_map[path] = " | ".join([f"{item['query']}({item['clicks']})" for item in top_kws])
            
        return final_map, None 
    
    except Exception as e:
        return {}, str(e)


# モバイル検索に限定して、検索順位（ポジション）を取得する関数
@st.cache_data(ttl=3600)
def get_mobile_search_ranking(site_url, days):
    try:
        creds_json = json.loads(st.secrets["gcp_service_account"])
        sc_creds = service_account.Credentials.from_service_account_info(
            creds_json, 
            scopes=['https://www.googleapis.com/auth/webmasters.readonly']
        )
        sc_service = build('searchconsole', 'v1', credentials=sc_creds)
        
        end_date = (datetime.now(JST) - timedelta(days=1)).strftime('%Y-%m-%d')
        start_date = (datetime.now(JST) - timedelta(days=days+1)).strftime('%Y-%m-%d')
        
        request = {
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': ['page', 'query'],
            'dimensionFilterGroups': [{
                'filters': [{'dimension': 'device', 'operator': 'equals', 'expression': 'MOBILE'}]
            }],
            'rowLimit': 10000
        }
        
        property_uri = f"sc-domain:{site_url}"
        try:
            response = sc_service.searchanalytics().query(siteUrl=property_uri, body=request).execute()
        except Exception:
            property_uri = f"https://{site_url}/"
            response = sc_service.searchanalytics().query(siteUrl=property_uri, body=request).execute()
            
        rows = response.get('rows', [])
        kw_map = {}
        for row in rows:
            page_url = row['keys'][0]
            query = row['keys'][1]
            clicks = row['clicks']
            position = row['position']
            
            if clicks > 0:
                if page_url not in kw_map:
                    kw_map[page_url] = []
                kw_map[page_url].append({"query": query, "clicks": clicks, "position": position})
                
        final_map = {}
        for page_url, kws in kw_map.items():
            path = urllib.parse.urlparse(page_url).path
            kws_sorted = sorted(kws, key=lambda x: x['clicks'], reverse=True)
            final_map[path] = kws_sorted[:3]
            
        return final_map, None 
    
    except Exception as e:
        return {}, str(e)


# ③ 記事ランキング
@st.cache_data(ttl=1800)
def get_article_ranking_raw(property_id, site_url, days):
    current_start = f"{days}daysAgo"
    current_end = "today"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    try:
        req_pv = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
            dimensions=[Dimension(name="pageTitle"), Dimension(name="pagePath"), Dimension(name="date")],
            metrics=[Metric(name="screenPageViews")],
            limit=10000
        )
        res_pv = client.run_report(req_pv)
        if not res_pv.rows: return pd.DataFrame(), None, {}

        base_data = []
        for row in res_pv.rows:
            base_data.append({
                "title": row.dimension_values[0].value,
                "path": row.dimension_values[1].value,
                "date": row.dimension_values[2].value,
                "pv": int(row.metric_values[0].value)
            })
        df_base = pd.DataFrame(base_data)
        
        unique_dates = sorted(list(df_base["date"].unique()))
        
        trend_map = {}
        for title, group in df_base.groupby("title"):
            daily_pv = dict(zip(group["date"], group["pv"]))
            trend = [daily_pv.get(d, 0) for d in unique_dates]
            trend_map[title] = trend

    except Exception:
        return pd.DataFrame(), None, {}

    source_map = {}
    try:
        req_src = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
            dimensions=[Dimension(name="pageTitle"), Dimension(name="sessionSourceMedium")],
            metrics=[Metric(name="screenPageViews")],
            limit=3000
        )
        res_src = client.run_report(req_src)
        if res_src.rows:
            temp_src_list = []
            for row in res_src.rows:
                temp_src_list.append({
                    "title": row.dimension_values[0].value,
                    "source": row.dimension_values[1].value,
                    "pv": int(row.metric_values[0].value)
                })
            df_src = pd.DataFrame(temp_src_list)
            for title, group in df_src.groupby("title"):
                top_srcs = group.sort_values("pv", ascending=False).head(3)["source"].tolist()
                source_map[title] = " | ".join([f"[{s}]" for s in top_srcs])
    except Exception:
        pass

    kw_map, sc_error = get_search_console_keywords(site_url, days)

    prev_pv_map = {}
    try:
        req_prev = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
            dimensions=[Dimension(name="pageTitle")],
            metrics=[Metric(name="screenPageViews")],
            limit=3000
        )
        res_prev = client.run_report(req_prev)
        if res_prev.rows:
            for row in res_prev.rows:
                prev_pv_map[row.dimension_values[0].value] = int(row.metric_values[0].value)
    except Exception:
        pass

    df_final = df_base.groupby("title").agg({"pv": "sum", "path": "first"}).reset_index()
    df_final["前期のPV"] = df_final["title"].map(prev_pv_map).fillna(0).astype(int)
    df_final["差分"] = df_final["pv"] - df_final["前期のPV"]
    
    def calc_pct(row):
        if row["前期のPV"] > 0: return f"{(row['差分'] / row['前期のPV'] * 100):+.1f}%"
        elif row["pv"] > 0: return "NEW"
        else: return "0%"
    df_final["前期間比"] = df_final.apply(calc_pct, axis=1)

    df_final["推移"] = df_final["title"].map(trend_map)
    df_final["上昇推移"] = df_final.apply(lambda r: r["推移"] if r["差分"] >= 0 else None, axis=1)
    df_final["下落推移"] = df_final.apply(lambda r: r["推移"] if r["差分"] < 0 else None, axis=1)

    def resolve_kw(row):
        path = str(row["path"]).split('?')[0]
        if path in kw_map and kw_map[path]:
            return kw_map[path]
        return "-"
        
    def resolve_source(row):
        title = row["title"]
        if title in source_map:
            return source_map[title]
        return "-"

    df_final["検索キーワード"] = df_final.apply(resolve_kw, axis=1)
    df_final["主な流入元"] = df_final.apply(resolve_source, axis=1)
    
    final = df_final.sort_values("pv", ascending=False).head(50)
    final = final[["title", "path", "pv", "前期のPV", "前期間比", "上昇推移", "下落推移", "検索キーワード", "主な流入元"]]
    final = final.rename(columns={"title": "記事タイトル", "pv": "今期のPV"})
    return final, sc_error, trend_map


# ④ SNS流入分析
@st.cache_data(ttl=1800)
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

# ★ 新規追加：全記事・全期間の下落分析レポートを取得する関数
@st.cache_data(ttl=3600)
def get_comprehensive_decline_report(property_id):
    # 比較する7つの期間を定義
    periods = [
        {"key": "昨日", "curr_start": "yesterday", "curr_end": "yesterday", "prev_start": "2daysAgo", "prev_end": "2daysAgo"},
        {"key": "1週間", "curr_start": "7daysAgo", "curr_end": "today", "prev_start": "15daysAgo", "prev_end": "8daysAgo"},
        {"key": "2週間", "curr_start": "14daysAgo", "curr_end": "today", "prev_start": "29daysAgo", "prev_end": "15daysAgo"},
        {"key": "1か月", "curr_start": "30daysAgo", "curr_end": "today", "prev_start": "61daysAgo", "prev_end": "31daysAgo"},
        {"key": "3か月", "curr_start": "90daysAgo", "curr_end": "today", "prev_start": "181daysAgo", "prev_end": "91daysAgo"},
        {"key": "6か月", "curr_start": "180daysAgo", "curr_end": "today", "prev_start": "361daysAgo", "prev_end": "181daysAgo"},
        {"key": "1年", "curr_start": "365daysAgo", "curr_end": "today", "prev_start": "731daysAgo", "prev_end": "366daysAgo"}
    ]
    
    article_map = {}
    
    for p in periods:
        k = p["key"]
        
        # 今期データ取得
        try:
            req_curr = RunReportRequest(
                property=f"properties/{property_id}",
                date_ranges=[DateRange(start_date=p["curr_start"], end_date=p["curr_end"])],
                dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
                metrics=[Metric(name="screenPageViews")],
                limit=100000
            )
            res_curr = client.run_report(req_curr)
            if res_curr.rows:
                for row in res_curr.rows:
                    path = row.dimension_values[0].value
                    title = row.dimension_values[1].value
                    pv = int(row.metric_values[0].value)
                    
                    if path not in article_map:
                        article_map[path] = {"URL(Path)": path, "記事タイトル": title}
                    article_map[path][f"{k}_今期PV"] = pv
        except Exception:
            pass
            
        # 前期データ取得
        try:
            req_prev = RunReportRequest(
                property=f"properties/{property_id}",
                date_ranges=[DateRange(start_date=p["prev_start"], end_date=p["prev_end"])],
                dimensions=[Dimension(name="pagePath")],
                metrics=[Metric(name="screenPageViews")],
                limit=100000
            )
            res_prev = client.run_report(req_prev)
            if res_prev.rows:
                for row in res_prev.rows:
                    path = row.dimension_values[0].value
                    pv = int(row.metric_values[0].value)
                    
                    if path not in article_map:
                        article_map[path] = {"URL(Path)": path, "記事タイトル": "不明"}
                    article_map[path][f"{k}_前期PV"] = pv
        except Exception:
            pass
            
    df_list = []
    for path, data in article_map.items():
        row_data = {"記事タイトル": data.get("記事タイトル", ""), "URL(Path)": path}
        # 各期間のデータを辞書にセット
        for p in periods:
            k = p["key"]
            curr = data.get(f"{k}_今期PV", 0)
            prev = data.get(f"{k}_前期PV", 0)
            diff = curr - prev
            
            row_data[f"{k} 今期"] = curr
            row_data[f"{k} 前期"] = prev
            row_data[f"{k} 増減"] = diff
            
        df_list.append(row_data)
        
    df = pd.DataFrame(df_list)
    # 下落分析が目的のため、「直近1か月の増減」がマイナス（落ち込みが大きい）順にソート
    if not df.empty:
        df = df.sort_values("1か月 増減", ascending=True)
        
    return df


# ---------------------------------------------------------
# 4. 画面表示
# ---------------------------------------------------------
st.write(f"最終更新: {now.strftime('%Y-%m-%d %H:%M:%S')}")

col_sel, _ = st.columns([1, 4])
with col_sel:
    period_days = st.selectbox("📅 分析期間 (レポート・順位分析用)", [3, 7, 14, 30], index=3, format_func=lambda x: f"過去 {x} 日間")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["⏱️ リアルタイムPV", "📈 期間分析レポート", "📱 SNSでの言及・流入", "🔍 検索順位・競合分析", "📉 アクセス下落分析"])

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
                pass
    if st.button("更新", key="refresh_realtime"):
        st.rerun()

with tab2:
    st.markdown("### 📈 期間比較レポート")
    for blog in BLOGS:
        with st.expander(f"📊 {blog['name']} の詳細分析", expanded=True):
            try:
                df_trend, curr_sum, prev_sum = get_daily_trend_comparison(blog["id"], period_days)
                df_top, sc_error, trend_map = get_article_ranking_raw(blog["id"], blog["url"], period_days)
                
                diff_total = curr_sum - prev_sum
                pct_total = (diff_total / prev_sum * 100) if prev_sum > 0 else 0
                st.markdown(f"#### 📅 総PV: {curr_sum:,} ({pct_total:+.1f}%)")
                
                if not df_trend.empty:
                    st.line_chart(df_trend, color=["#FF4B4B", "#CCCCCC"]) 
                    st.caption("赤線: 今期 / グレー線: 前期")

                if not df_top.empty and not df_trend.empty:
                    df_article_trend = pd.DataFrame(index=df_trend.index)
                    for i, title in enumerate(df_top["記事タイトル"]):
                        short_title = title[:15] + "..." if len(title) > 15 else title
                        col_name = f"{i+1}位: {short_title}"
                        
                        trend_data = trend_map.get(title, [])
                        if len(trend_data) < len(df_trend):
                            trend_data = trend_data + [0] * (len(df_trend) - len(trend_data))
                        
                        df_article_trend[col_name] = trend_data[:len(df_trend)]
                    
                    st.markdown("#### 📊 TOP50記事のPV推移")
                    st.line_chart(df_article_trend)
                    st.caption("※ 上位50記事の推移です。グラフ上の凡例をダブルクリックすると特定の記事だけを表示できます。")

                if sc_error:
                    st.warning("サーチコンソール連携エラーが発生しています。")
                
                if not df_top.empty:
                    st.markdown("#### 🏆 記事別ランキング TOP50")
                    st.dataframe(
                        df_top, 
                        column_config={
                            "記事タイトル": st.column_config.TextColumn("記事タイトル", width="medium"),
                            "path": None, 
                            "上昇推移": st.column_config.LineChartColumn("上昇 📈", y_min=0, color="#FF4B4B"),
                            "下落推移": st.column_config.LineChartColumn("下落 📉", y_min=0, color="#1E88E5"),
                            "検索キーワード": st.column_config.TextColumn("検索キーワード", width="large"),
                            "主な流入元": st.column_config.TextColumn("主な流入元", width="medium"),
                        },
                        use_container_width=False, 
                        hide_index=True, 
                        height=800
                    )
                else:
                    st.warning("データなし")
            except Exception as e:
                st.error(f"全体処理エラー: {e}")

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
                st.error(f"SNSエラー: {e}")
            st.markdown("---")
            q = urllib.parse.quote(blog.get("url", "")) 
            if q:
                c1, c2 = st.columns(2)
                c1.link_button("X(Twitter)反応", f"https://search.yahoo.co.jp/realtime/search?p={q}")
                c2.link_button("SNS全体Google検索", f"https://www.google.com/search?q=site:x.com+{q}+OR+site:facebook.com+{q}")

with tab4:
    st.markdown("### 🔍 検索順位・競合分析（モバイル検索）")
    for blog in BLOGS:
        with st.expander(f"🏆 {blog['name']} - 流入上位キーワードの検索順位", expanded=True):
            try:
                df_top, _, _ = get_article_ranking_raw(blog["id"], blog["url"], period_days)
                mobile_kw_map, m_error = get_mobile_search_ranking(blog["url"], period_days)
                
                if m_error:
                    st.error(f"Search Consoleエラー: {m_error}")
                
                if not df_top.empty:
                    rank_list = []
                    for i, row in df_top.iterrows():
                        title = row["記事タイトル"]
                        path = str(row["path"]).split('?')[0]
                        pv = row["今期のPV"]
                        
                        kws = mobile_kw_map.get(path, [])
                        kw1 = kws[0] if len(kws) > 0 else None
                        kw2 = kws[1] if len(kws) > 1 else None
                        kw3 = kws[2] if len(kws) > 2 else None
                        
                        def format_kw(k):
                            if not k: return "-"
                            pos = round(k["position"], 1)
                            pos_str = f"{pos}位" if pos <= 50 else "50位圏外"
                            return f"{k['query']} ({pos_str})"

                        rank_list.append({
                            "記事タイトル": title,
                            "今期のPV": pv,
                            "No.1 キーワード": format_kw(kw1),
                            "No.2 キーワード": format_kw(kw2),
                            "No.3 キーワード": format_kw(kw3),
                        })
                    
                    df_rank = pd.DataFrame(rank_list)
                    st.dataframe(
                        df_rank,
                        column_config={
                            "記事タイトル": st.column_config.TextColumn("記事タイトル", width="medium"),
                            "今期のPV": st.column_config.NumberColumn("今期のPV"),
                            "No.1 キーワード": st.column_config.TextColumn("No.1 キーワード (順位)", width="medium"),
                            "No.2 キーワード": st.column_config.TextColumn("No.2 キーワード (順位)", width="medium"),
                            "No.3 キーワード": st.column_config.TextColumn("No.3 キーワード (順位)", width="medium"),
                        },
                        use_container_width=True,
                        hide_index=True,
                        height=800
                    )
                else:
                    st.warning("データがありません")
            except Exception as e:
                st.error(f"エラー: {e}")

# ★ 新規追加タブ：全記事アクセス下落分析
with tab5:
    st.markdown("### 📉 全記事アクセス推移・下落分析レポート")
    st.info("💡 **全記事**について、「昨日」「1週間」「2週間」「1ヶ月」「3ヶ月」「6ヶ月」「1年」のそれぞれのアクセス推移と前期比較を一括抽出し、アクセスが落ちている記事を特定します。（※API通信を複数回行うため、取得に少し時間がかかります）")
    
    selected_blog = st.selectbox("分析するブログを選択", [b["name"] for b in BLOGS])
    blog_info = next((b for b in BLOGS if b["name"] == selected_blog), None)
    
    if st.button("詳細レポートを生成する（CSV出力可）", type="primary"):
        with st.spinner("GA4から全期間のデータを抽出しています...（数分かかる場合があります）"):
            df_decline = get_comprehensive_decline_report(blog_info["id"])
            
            if not df_decline.empty:
                st.success("レポートの生成が完了しました！ **「直近1ヶ月」でアクセス下落幅が大きい記事から順番に** 表示しています。")
                
                # 日本語が文字化けしないよう utf-8-sig でエンコードしてCSV化
                csv = df_decline.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="📥 CSVをダウンロードしてExcelで詳細分析",
                    data=csv,
                    file_name=f"decline_report_{blog_info['url']}_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                )
                
                st.dataframe(df_decline, use_container_width=True, hide_index=True, height=800)
            else:
                st.warning("データが取得できませんでした。")
