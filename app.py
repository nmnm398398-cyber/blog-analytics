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

# Search Console キーワード取得
@st.cache_data(ttl=3600)
def get_search_console_keywords(site_url, days):
    try:
        creds_json = json.loads(st.secrets["gcp_service_account"])
        sc_creds = service_account.Credentials.from_service_account_info(
            creds_json, scopes=['https://www.googleapis.com/auth/webmasters.readonly']
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

# モバイル順位取得
@st.cache_data(ttl=3600)
def get_mobile_search_ranking(site_url, days):
    try:
        creds_json = json.loads(st.secrets["gcp_service_account"])
        sc_creds = service_account.Credentials.from_service_account_info(
            creds_json, scopes=['https://www.googleapis.com/auth/webmasters.readonly']
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
        if path in kw_map and kw_map[path]: return kw_map[path]
        return "-"
    def resolve_source(row):
        title = row["title"]
        if title in source_map: return source_map[title]
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

# 下落分析
@st.cache_data(ttl=3600)
def get_comprehensive_decline_report(property_id):
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
    if not df.empty:
        df = df.sort_values("1か月 増減", ascending=True)
    return df

# タイトル改善
@st.cache_data(ttl=3600)
def get_improvement_data(property_id, site_url, days):
    ga4_data = {}
    try:
        req_ga4 = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
            dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
            metrics=[Metric(name="screenPageViews"), Metric(name="averageSessionDuration")],
            limit=5000
        )
        res_ga4 = client.run_report(req_ga4)
        if res_ga4.rows:
            for row in res_ga4.rows:
                path = row.dimension_values[0].value.split('?')[0]
                title = row.dimension_values[1].value
                pv = int(row.metric_values[0].value)
                duration = float(row.metric_values[1].value)
                if path not in ga4_data:
                    ga4_data[path] = {"title": title, "pv": pv, "duration": duration}
                else:
                    ga4_data[path]["pv"] += pv
    except Exception:
        pass

    gsc_data = {}
    try:
        creds_json = json.loads(st.secrets["gcp_service_account"])
        sc_creds = service_account.Credentials.from_service_account_info(
            creds_json, scopes=['https://www.googleapis.com/auth/webmasters.readonly']
        )
        sc_service = build('searchconsole', 'v1', credentials=sc_creds)
        
        end_date = (datetime.now(JST) - timedelta(days=1)).strftime('%Y-%m-%d')
        start_date = (datetime.now(JST) - timedelta(days=days+1)).strftime('%Y-%m-%d')
        
        request = {
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': ['page'],
            'rowLimit': 5000
        }
        property_uri = f"sc-domain:{site_url}"
        try:
            response = sc_service.searchanalytics().query(siteUrl=property_uri, body=request).execute()
        except Exception:
            property_uri = f"https://{site_url}/"
            response = sc_service.searchanalytics().query(siteUrl=property_uri, body=request).execute()
            
        for row in response.get('rows', []):
            page_url = row['keys'][0]
            path = urllib.parse.urlparse(page_url).path
            gsc_data[path] = {
                "impressions": row["impressions"],
                "clicks": row["clicks"],
                "ctr": row["ctr"],
                "position": row["position"]
            }
    except Exception:
        pass

    result = []
    for path, g_val in ga4_data.items():
        if g_val["pv"] < 10: continue
        s_val = gsc_data.get(path, {"impressions": 0, "clicks": 0, "ctr": 0, "position": 0})
        
        title_alert = "⚠️改善" if s_val["impressions"] > 100 and s_val["ctr"] < 0.02 else "OK"
        content_alert = "⚠️リライト" if g_val["pv"] > 50 and g_val["duration"] < 30 else "OK"

        result.append({
            "記事タイトル": g_val["title"],
            "URL(Path)": path,
            "GA4_PV": g_val["pv"],
            "GA4_平均滞在時間(秒)": round(g_val["duration"], 1),
            "滞在時間判定": content_alert,
            "GSC_表示回数": s_val["impressions"],
            "GSC_クリック": s_val["clicks"],
            "GSC_CTR(%)": round(s_val["ctr"] * 100, 2),
            "GSC_平均順位": round(s_val["position"], 1),
            "タイトル判定": title_alert
        })
        
    df = pd.DataFrame(result)
    if not df.empty:
        df = df.sort_values("GSC_表示回数", ascending=False)
    return df

@st.cache_data(ttl=3600)
def get_gsc_queries_for_page(site_url, page_path, days):
    try:
        creds_json = json.loads(st.secrets["gcp_service_account"])
        sc_creds = service_account.Credentials.from_service_account_info(
            creds_json, scopes=['https://www.googleapis.com/auth/webmasters.readonly']
        )
        sc_service = build('searchconsole', 'v1', credentials=sc_creds)
        
        end_date = (datetime.now(JST) - timedelta(days=1)).strftime('%Y-%m-%d')
        start_date = (datetime.now(JST) - timedelta(days=days+1)).strftime('%Y-%m-%d')
        
        request = {
            'startDate': start_date,
            'endDate': end_date,
            'dimensions': ['query'],
            'dimensionFilterGroups': [{
                'filters': [{'dimension': 'page', 'operator': 'contains', 'expression': page_path}]
            }],
            'rowLimit': 50
        }
        
        property_uri = f"sc-domain:{site_url}"
        try:
            response = sc_service.searchanalytics().query(siteUrl=property_uri, body=request).execute()
        except Exception:
            property_uri = f"https://{site_url}/"
            response = sc_service.searchanalytics().query(siteUrl=property_uri, body=request).execute()
            
        rows = response.get('rows', [])
        result = []
        for row in rows:
            q = row['keys'][0]
            search_url = f"https://www.google.com/search?q={urllib.parse.quote(q)}"
            result.append({
                "検索キーワード": q,
                "表示回数": row["impressions"],
                "クリック": row["clicks"],
                "CTR(%)": round(row["ctr"] * 100, 2),
                "平均順位": round(row["position"], 1),
                "競合比較リンク": search_url
            })
            
        return pd.DataFrame(result)
    except Exception as e:
        return pd.DataFrame()

# ★ 新規追加：順位低下＆アクセス減に基づくリライト提案ロジック
@st.cache_data(ttl=3600)
def get_rewrite_proposals(property_id, site_url, days):
    curr_start = f"{days}daysAgo"
    curr_end = "yesterday"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    # GA4 今期PV
    ga4_curr = {}
    try:
        req = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=curr_start, end_date=curr_end)],
            dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
            metrics=[Metric(name="screenPageViews")],
            limit=10000
        )
        res = client.run_report(req)
        for row in res.rows:
            p = row.dimension_values[0].value.split('?')[0]
            t = row.dimension_values[1].value
            pv = int(row.metric_values[0].value)
            ga4_curr[p] = {"title": t, "pv_curr": pv}
    except Exception: pass

    # GA4 前期PV
    ga4_prev = {}
    try:
        req = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            limit=10000
        )
        res = client.run_report(req)
        for row in res.rows:
            p = row.dimension_values[0].value.split('?')[0]
            pv = int(row.metric_values[0].value)
            ga4_prev[p] = pv
    except Exception: pass

    # GSC データ取得用ヘルパー
    def fetch_gsc(start_dt, end_dt):
        try:
            creds_json = json.loads(st.secrets["gcp_service_account"])
            sc_creds = service_account.Credentials.from_service_account_info(
                creds_json, scopes=['https://www.googleapis.com/auth/webmasters.readonly']
            )
            sc_service = build('searchconsole', 'v1', credentials=sc_creds)
            req_body = {'startDate': start_dt, 'endDate': end_dt, 'dimensions': ['page'], 'rowLimit': 10000}
            try:
                res = sc_service.searchanalytics().query(siteUrl=f"sc-domain:{site_url}", body=req_body).execute()
            except:
                res = sc_service.searchanalytics().query(siteUrl=f"https://{site_url}/", body=req_body).execute()
            
            data = {}
            for row in res.get('rows', []):
                url = row['keys'][0]
                path = urllib.parse.urlparse(url).path
                data[path] = {"pos": row["position"], "clicks": row["clicks"]}
            return data
        except:
            return {}

    now_j = datetime.now(JST)
    c_end_dt = (now_j - timedelta(days=1)).strftime('%Y-%m-%d')
    c_start_dt = (now_j - timedelta(days=days)).strftime('%Y-%m-%d')
    p_end_dt = (now_j - timedelta(days=days+1)).strftime('%Y-%m-%d')
    p_start_dt = (now_j - timedelta(days=days*2)).strftime('%Y-%m-%d')

    gsc_curr = fetch_gsc(c_start_dt, c_end_dt)
    gsc_prev = fetch_gsc(p_start_dt, p_end_dt)

    result = []
    for path, curr_val in ga4_curr.items():
        title = curr_val["title"]
        pv_c = curr_val["pv_curr"]
        pv_p = ga4_prev.get(path, 0)
        
        gc = gsc_curr.get(path, {"pos": 0.0})
        gp = gsc_prev.get(path, {"pos": 0.0})
        pos_c = gc["pos"]
        pos_p = gp["pos"]
        
        # 最低限のトラフィックがない記事は除外
        if pv_p < 20 and pos_p == 0: continue
        
        pv_diff = pv_c - pv_p
        # 順位変動: 数値が大きくなる＝順位が落ちている
        pos_diff = pos_c - pos_p if pos_p > 0 and pos_c > 0 else 0
        
        reason = []
        if pos_diff >= 2.0: # 順位が2位以上ダウン
            reason.append(f"順位低下 (▼{pos_diff:.1f})")
        if pv_diff <= -20 or (pv_p > 0 and pv_diff/pv_p <= -0.2): # PVが20以上減、または20%以上減
            reason.append(f"アクセス減 (▼{abs(pv_diff)})")
            
        priority = ""
        if len(reason) == 2: priority = "🚨 最優先"
        elif len(reason) == 1: priority = "⚠️ 要検討"
        else: priority = "✅ 安定"
            
        if priority != "✅ 安定":
            result.append({
                "優先度": priority,
                "低下要因": " ＋ ".join(reason),
                "記事タイトル": title,
                "今期PV": pv_c,
                "前期PV": pv_p,
                "今期平均順位": round(pos_c, 1) if pos_c > 0 else "-",
                "前期平均順位": round(pos_p, 1) if pos_p > 0 else "-",
                "URL(Path)": path
            })

    df = pd.DataFrame(result)
    if not df.empty:
        df["sort_val"] = df["優先度"].apply(lambda x: 1 if "最優先" in x else 2)
        df = df.sort_values(["sort_val", "前期PV"], ascending=[True, False]).drop(columns=["sort_val"])
        
    return df


# ---------------------------------------------------------
# 4. 画面表示
# ---------------------------------------------------------
st.write(f"最終更新: {now.strftime('%Y-%m-%d %H:%M:%S')}")

col_sel, _ = st.columns([1, 4])
with col_sel:
    period_days = st.selectbox("📅 分析期間", [3, 7, 14, 30], index=3, format_func=lambda x: f"過去 {x} 日間")

# ★ タブを7つに変更
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "⏱️ リアルタイムPV", 
    "📈 期間分析レポート", 
    "📱 SNS流入", 
    "🔍 検索順位", 
    "📉 アクセス下落分析",
    "🛠️ タイトル・コンテンツ改善",
    "📝 リライト提案・順位低下"
])

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
                
                if not df_top.empty:
                    st.markdown("#### 🏆 記事別ランキング TOP50")
                    st.dataframe(
                        df_top, 
                        column_config={
                            "記事タイトル": st.column_config.TextColumn("記事タイトル", width="medium"),
                            "path": None, 
                            "上昇推移": st.column_config.LineChartColumn("上昇 📈", y_min=0, color="#FF4B4B"),
                            "下落推移": st.column_config.LineChartColumn("下落 📉", y_min=0, color="#1E88E5"),
                        },
                        use_container_width=False, hide_index=True, height=800
                    )
            except Exception as e:
                st.error(f"全体処理エラー: {e}")

with tab3:
    st.markdown("### 📱 SNS流入 & エゴサーチ")
    for blog in BLOGS:
        with st.expander(f"💬 {blog['name']}", expanded=True):
            try:
                df_sns = get_sns_traffic_safe(blog["id"], blog["url"], 7)
                if not df_sns.empty:
                    st.bar_chart(df_sns.groupby("SNS")["PV"].sum(), color="#1DA1F2")
                    st.dataframe(df_sns, column_config={"search_link": st.column_config.LinkColumn("投稿", display_text="検索 🔎")}, use_container_width=True, hide_index=True)
            except Exception:
                pass

with tab4:
    st.markdown("### 🔍 検索順位・競合分析（モバイル検索）")
    for blog in BLOGS:
        with st.expander(f"🏆 {blog['name']} - 流入上位キーワード", expanded=True):
            try:
                df_top, _, _ = get_article_ranking_raw(blog["id"], blog["url"], period_days)
                mobile_kw_map, m_error = get_mobile_search_ranking(blog["url"], period_days)
                if not df_top.empty:
                    rank_list = []
                    for i, row in df_top.iterrows():
                        kws = mobile_kw_map.get(str(row["path"]).split('?')[0], [])
                        def format_kw(k):
                            if not k: return "-"
                            return f"{k['query']} ({round(k['position'], 1)}位)" if k['position'] <= 50 else f"{k['query']} (圏外)"
                        rank_list.append({
                            "記事タイトル": row["記事タイトル"],
                            "PV": row["今期のPV"],
                            "No.1": format_kw(kws[0] if len(kws)>0 else None),
                            "No.2": format_kw(kws[1] if len(kws)>1 else None),
                            "No.3": format_kw(kws[2] if len(kws)>2 else None),
                        })
                    st.dataframe(pd.DataFrame(rank_list), use_container_width=True, hide_index=True, height=800)
            except Exception:
                pass

with tab5:
    st.markdown("### 📉 全記事アクセス推移・下落分析レポート")
    selected_blog = st.selectbox("分析するブログを選択", [b["name"] for b in BLOGS], key="tab5_blog")
    blog_info = next((b for b in BLOGS if b["name"] == selected_blog), None)
    
    if st.button("詳細レポートを生成する（CSV出力可）", type="primary"):
        with st.spinner("データを抽出しています..."):
            df_decline = get_comprehensive_decline_report(blog_info["id"])
            if not df_decline.empty:
                st.success("「直近1ヶ月」でアクセス下落幅が大きい順に表示しています。")
                csv = df_decline.to_csv(index=False).encode('utf-8-sig')
                st.download_button("📥 CSVをダウンロード", data=csv, file_name=f"decline_{blog_info['url']}.csv", mime="text/csv")
                st.dataframe(df_decline, use_container_width=True, hide_index=True, height=800)

with tab6:
    st.markdown("### 🛠️ タイトル・コンテンツ改善分析")
    selected_blog_t6 = st.selectbox("分析するブログを選択", [b["name"] for b in BLOGS], key="tab6_blog")
    blog_info_t6 = next(b for b in BLOGS if b["name"] == selected_blog_t6)
    
    with st.spinner("パフォーマンスデータを取得しています..."):
        df_improve = get_improvement_data(blog_info_t6["id"], blog_info_t6["url"], period_days)
    
    sub1, sub2 = st.tabs(["📊 ① タイトル・滞在時間 分析リスト", "🔎 ② 検索意図ズレ・競合ギャップ分析ツール"])
    
    with sub1:
        st.markdown("#### 🚨 改善対象記事リスト")
        st.markdown("- **タイトル判定**: 表示回数100回以上かつCTR 2%未満で「⚠️改善」\n- **滞在時間判定**: PV50以上かつ平均滞在時間30秒未満で「⚠️リライト」")
        if not df_improve.empty:
            st.dataframe(
                df_improve,
                column_config={"記事タイトル": st.column_config.TextColumn(width="medium"), "URL(Path)": None},
                use_container_width=True, hide_index=True, height=600
            )
        else:
            st.warning("データがありません")
            
    with sub2:
        st.markdown("#### 🔎 特定記事の検索意図ズレ ＆ コンテンツギャップ分析")
        if not df_improve.empty:
            title_to_path = {f"{row['記事タイトル']} (PV:{row['GA4_PV']})": row['URL(Path)'] for _, row in df_improve.head(100).iterrows()}
            selected_title = st.selectbox("分析する記事を選択（PV上位100件から）", list(title_to_path.keys()))
            target_path = title_to_path[selected_title]
            
            with st.spinner("対象記事の検索クエリデータを取得中..."):
                df_queries = get_gsc_queries_for_page(blog_info_t6["url"], target_path, period_days)
            
            if not df_queries.empty:
                st.markdown(f"**「{selected_title}」の流入クエリ一覧**")
                st.dataframe(df_queries, column_config={"競合比較リンク": st.column_config.LinkColumn("Googleで競合を確認", display_text="検索する 🔎")}, use_container_width=True, hide_index=True)
            else:
                st.warning("この記事の検索クエリデータはまだありません。")

# ★ 新規追加タブ：リライト提案・順位低下アラート
with tab7:
    st.markdown("### 📝 リライト提案・順位低下アラート")
    st.info("💡 **「前期と比較して検索順位が2位以上落ちた記事」**や**「PVが大きく落ちた記事」**を自動抽出し、テコ入れすべき対象をリストアップします。")
    
    selected_blog_t7 = st.selectbox("分析するブログを選択", [b["name"] for b in BLOGS], key="tab7_blog")
    blog_info_t7 = next(b for b in BLOGS if b["name"] == selected_blog_t7)
    
    if st.button("🚨 リライト対象記事を抽出する", type="primary"):
        with st.spinner(f"GA4とSearch Consoleのデータを照合しています...（過去 {period_days} 日間 vs その前の {period_days} 日間）"):
            df_rewrite = get_rewrite_proposals(blog_info_t7["id"], blog_info_t7["url"], period_days)
            
            if not df_rewrite.empty:
                st.success("抽出完了！「順位低下」と「アクセス減」の両方が起きている記事は『🚨最優先』として上に表示されます。")
                st.dataframe(
                    df_rewrite,
                    column_config={
                        "優先度": st.column_config.TextColumn("優先度", width="small"),
                        "低下要因": st.column_config.TextColumn("低下要因", width="medium"),
                        "記事タイトル": st.column_config.TextColumn("記事タイトル", width="medium"),
                        "URL(Path)": None # 表示上は隠す
                    },
                    use_container_width=True,
                    hide_index=True,
                    height=800
                )
            else:
                st.success("素晴らしい！直近で大きく順位やアクセスを落としている記事は見つかりませんでした。")
