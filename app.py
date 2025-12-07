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

# ① リアルタイムPV (今日 vs 昨日)
def get_realtime_metrics(property_id):
    # 今日の累計
    req_today = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="today", end_date="today")],
        metrics=[Metric(name="screenPageViews")],
    )
    res_today = client.run_report(req_today)
    pv_today = int(res_today.rows[0].metric_values[0].value) if res_today.rows else 0

    # 昨日の同時刻
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

# ② 期間全体の総PV比較 (Current vs Previous)
def get_total_period_comparison(property_id, days):
    """指定期間とその前の期間の総PVを比較"""
    # 期間設定
    current_start = f"{days}daysAgo"
    current_end = "today"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    request = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[
            DateRange(start_date=current_start, end_date=current_end),
            DateRange(start_date=prev_start, end_date=prev_end)
        ],
        metrics=[Metric(name="screenPageViews")],
    )
    response = client.run_report(request)
    
    current_pv = 0
    prev_pv = 0
    
    if response.rows:
        # GA4 APIは date_ranges を指定すると行が返ってくる可能性があるが
        # metric_valuesだけでは区別しづらいため、単純化して2回クエリを投げるほうが確実だが、
        # ここでは簡易実装として値を解析する。
        # 安全のため、シンプルに2回リクエストに変更して確実性を担保します。
        pass

    # 確実な実装: 2回取得
    req_curr = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=current_start, end_date=current_end)],
        metrics=[Metric(name="screenPageViews")]
    )
    res_curr = client.run_report(req_curr)
    current_pv = int(res_curr.rows[0].metric_values[0].value) if res_curr.rows else 0

    req_prev = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
        metrics=[Metric(name="screenPageViews")]
    )
    res_prev = client.run_report(req_prev)
    prev_pv = int(res_prev.rows[0].metric_values[0].value) if res_prev.rows else 0

    return current_pv, prev_pv

# ③ 記事ランキング比較 (Current Top 30 vs Previous)
def get_article_ranking_comparison(property_id, days):
    current_start = f"{days}daysAgo"
    current_end = "today"
    prev_start = f"{days*2}daysAgo"
    prev_end = f"{days+1}daysAgo"

    # --- A. 今期のデータ取得 (タイトル + 流入元/キーワード) ---
    try:
        # まずキーワード取得にトライ
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
                    clean_info = info if info and info not in ["(not set)", "(not provided)"] else ""
                    raw_data.append({"title": title, "info": clean_info, "pv": pv})

    except Exception:
        # エラーなら流入元にフォールバック
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

    # 今期の集計 (記事ごと)
    df_curr_grouped = df_curr.groupby("title")["pv"].sum().reset_index().rename(columns={"pv": "今期のPV"})
    
    # 流入情報の結合
    info_data = df_curr[df_curr["info"] != ""].sort_values("pv", ascending=False)
    def get_top_infos(title):
        infos = info_data[info_data["title"] == title]["info"].head(3).tolist()
        return ", ".join(infos) if infos else "-"
    
    col_info_name = "検索キーワード(TOP3)" if is_keyword else "主な流入元(TOP3)"
    df_curr_grouped[col_info_name] = df_curr_grouped["title"].apply(get_top_infos)

    # --- B. 前期のデータ取得 (タイトルのみでOK) ---
    req_prev = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date=prev_start, end_date=prev_end)],
        dimensions=[Dimension(name="pageTitle")],
        metrics=[Metric(name="screenPageViews")],
        limit=2000 # 多めに取得してマッチさせる
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
    # 今期のデータに前期のデータを結合 (Left Join)
    merged_df = pd.merge(df_curr_grouped, df_prev, on="title", how="left")
    merged_df["前期のPV"] = merged_df["前期のPV"].fillna(0).astype(int)
    
    # 差分計算
    merged_df["差分"] = merged_df["今期のPV"] - merged_df["前期のPV"]
    
    # ソート (TOP30多い順)
    final_df = merged_df.sort_values("今期のPV", ascending=False).head(30)
    
    # カラム整理
    final_df = final_df[["title", "今期のPV", "前期のPV", "差分", col_info_name]]
    final_df = final_df.rename(columns={"title": "記事タイトル"})
    
    return final_df

# ---------------------------------------------------------
# 4. 画面表示
# ---------------------------------------------------------
st.write(f"最終更新: {now.strftime('%Y-%m-%d %H:%M:%S')}")

tab1, tab2 = st.tabs(["⏱️ リアルタイムPV", "📈 期間比較・ランキング"])

# --- タブ1: リアルタイム ---
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

# --- タブ2: 期間比較分析 ---
with tab2:
    st.markdown("### 📈 期間比較レポート")
    
    # 期間選択
    col_sel, _ = st.columns([1, 2])
    with col_sel:
        period_days = st.selectbox(
            "分析期間を選択", 
            [7, 30], 
            index=1, 
            format_func=lambda x: f"過去 {x} 日間 vs その前の {x} 日間"
        )
    
    for blog in BLOGS:
        with st.expander(f"📊 {blog['name']} の分析結果", expanded=True):
            try:
                # 1. 全体サマリー取得
                curr_total, prev_total = get_total_period_comparison(blog["id"], period_days)
                diff_total = curr_total - prev_total
                pct_total = (diff_total / prev_total * 100) if prev_total > 0 else 0
                
                # サマリー表示
                st.markdown("#### 📅 全体のPV推移")
                col_m1, col_m2, col_m3 = st.columns(3)
                col_m1.metric("今期の総PV", f"{curr_total:,}", f"{diff_total:+,} ({pct_total:+.1f}%)")
                col_m2.metric("前期の総PV", f"{prev_total:,}")
                
                # 2. 詳細ランキング取得
                df_top = get_article_ranking_comparison(blog["id"], period_days)
                
                if not df_top.empty:
                    st.markdown("#### 🏆 記事別ランキング TOP30 (多い順)")
                    
                    # グラフ (今期PV)
                    st.bar_chart(df_top.set_index("記事タイトル")["今期のPV"], color="#FF4B4B")
                    
                    # テーブル表示
                    # dataframeのスタイル機能を使って、差分を見やすくすることも可能ですが、
                    # まずはシンプルに表示します
                    st.dataframe(
                        df_top, 
                        use_container_width=True, 
                        hide_index=True,
                        height=600
                    )
                else:
                    st.warning("データがありません")
                    
            except Exception as e:
                st.error(f"分析エラー: {e}")
