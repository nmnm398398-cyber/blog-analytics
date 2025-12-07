import streamlit as st
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Metric, Dimension,
)
from datetime import datetime
import json
import pytz

# ページ設定
st.set_page_config(page_title="Blog PV Dashboard", layout="wide")
st.title("📈 ブログPV リアルタイム比較")

# 現在時刻（日本時間）
JST = pytz.timezone('Asia/Tokyo')
now = datetime.now(JST)
current_hour = now.hour

st.write(f"取得時刻: {now.strftime('%Y-%m-%d %H:%M:%S')}")

# ---------------------------------------------------------
# 1. 認証 (Streamlit CloudのSecretsを使う)
# ---------------------------------------------------------
try:
    # secretsのキー名は "gcp_service_account" とします
    creds_json = json.loads(st.secrets["gcp_service_account"])
    client = BetaAnalyticsDataClient.from_service_account_info(creds_json)
except Exception as e:
    st.error(f"認証エラー: Secretsの設定を確認してください。\n{e}")
    st.stop()

# ---------------------------------------------------------
# 2. ブログ設定 (頂いたIDを反映済み)
# ---------------------------------------------------------
BLOGS = [
    {"name": "🚙 ジムニーフリーク！", "id": "470121869"},
    {"name": "🎣 ソルトルアーのすすめ！", "id": "343862616"},
    {"name": "👔 公務員転職マン", "id": "445135719"},
]

# ---------------------------------------------------------
# 3. データ取得ロジック
# ---------------------------------------------------------
def get_blog_metrics(property_id):
    # A. 今日の累計 (0:00 ~ 現在)
    req_today = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="today", end_date="today")],
        metrics=[Metric(name="screenPageViews")],
    )
    res_today = client.run_report(req_today)
    pv_today = int(res_today.rows[0].metric_values[0].value) if res_today.rows else 0

    # B. 昨日の時間別データ
    req_yest = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="yesterday", end_date="yesterday")],
        dimensions=[Dimension(name="hour")],
        metrics=[Metric(name="screenPageViews")],
    )
    res_yest = client.run_report(req_yest)
    
    pv_yest_same = 0 # 昨日同時刻まで
    pv_yest_total = 0 # 昨日合計
    
    if res_yest.rows:
        for row in res_yest.rows:
            h = int(row.dimension_values[0].value)
            pv = int(row.metric_values[0].value)
            pv_yest_total += pv
            if h <= current_hour:
                pv_yest_same += pv
                
    return pv_today, pv_yest_same, pv_yest_total

# ---------------------------------------------------------
# 4. 表示
# ---------------------------------------------------------
cols = st.columns(3)

for i, blog in enumerate(BLOGS):
    with cols[i]:
        st.subheader(blog["name"])
        try:
            today, yest_same, yest_total = get_blog_metrics(blog["id"])
            
            diff = today - yest_same
            percent = (diff / yest_same * 100) if yest_same > 0 else 0
            
            st.metric(
                label="今日のPV",
                value=f"{today:,}",
                delta=f"{diff:+,} ({percent:+.1f}%) vs昨日同時刻"
            )
            st.caption(f"昨日同時刻: {yest_same:,} PV / 昨日合計: {yest_total:,} PV")
            
        except Exception as e:
            st.error("データ取得失敗")
            st.caption(str(e))

if st.button("更新"):
    st.rerun()