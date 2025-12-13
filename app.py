# ⑤ 接続診断モード (エラー判定強化版)
def run_diagnostic(property_id):
    st.write("---")
    st.markdown(f"#### 🕵️ GA4 × SearchConsole 接続診断レポート (ID: `{property_id}`)")
    
    try:
        # A. 単純なキーワードデータの存在確認 (過去30日)
        req = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date="30daysAgo", end_date="today")],
            dimensions=[Dimension(name="organicGoogleSearchQuery")],
            metrics=[Metric(name="screenPageViews")],
            limit=100
        )
        res = client.run_report(req)
        
        total_rows = 0
        not_set_count = 0
        valid_kw_sample = []

        if res.rows:
            total_rows = len(res.rows)
            for row in res.rows:
                kw = row.dimension_values[0].value
                if kw in ["(not set)", "(not provided)", ""]:
                    not_set_count += 1
                else:
                    valid_kw_sample.append(kw)
        
        st.write(f"**取得データ数:** {total_rows} 行")
        
        if len(valid_kw_sample) > 0:
            st.success(f"✅ **接続成功！** {len(valid_kw_sample)} 個の有効なキーワードが見つかりました。")
            st.markdown(f"**検出されたキーワード例:** `{', '.join(valid_kw_sample[:5])}` ...")
        else:
            st.warning("⚠️ 接続はできていますが、有効なキーワードがまだ0件です。（(not set)のみ）")
            st.write("→ プライバシー保護機能が働いている可能性があります。「デバイスベース」への変更を試してください。")
            
    except Exception as e:
        # エラーメッセージを文字列にする
        err_msg = str(e)
        
        st.error("❌ **致命的な接続エラーが発生しました**")
        
        # 今回の「400 organicGoogleSearchQuery is not a valid dimension」を検知
        if "not a valid dimension" in err_msg or "organicGoogleSearchQuery" in err_msg:
            st.error("""
            **原因特定: プロパティIDの不一致、または連携未設定**
            
            GA4 APIが「検索キーワード機能なんて、このプロパティには存在しない」と言っています。
            以下の可能性が非常に高いです。
            
            1. **ID間違い:** app.pyに書いたID「{}」と、実際に連携作業をしたGA4のプロパティIDが違います。
            2. **連携先間違い:** 別のGA4プロパティ（例: テスト用）に連携していませんか？
            
            GA4管理画面で「プロパティID」を確認し、コードを修正してください。
            """.format(property_id))
        else:
            st.error(f"APIエラー詳細: {e}")
