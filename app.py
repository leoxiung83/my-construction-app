import streamlit as st
import pandas as pd
import os
import json
import time
import shutil
import copy
import altair as alt
import streamlit.components.v1 as components
import zipfile
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# ==========================================
# 0. 系統設定
# ==========================================
st.set_page_config(page_title="多專案施工管理系統 (安全登入版)", layout="wide", page_icon="🔒")

# --- 🔐 安全設定 ---
SYSTEM_PASSWORD = "12345" 

# --- 檔案路徑 ---
DATA_FILE = 'construction_data.csv' 
SETTINGS_FILE = 'settings.json'
TYPES_FILE = 'category_types.json'
PRICES_FILE = 'item_prices.json'
KEY_FILE = 'service_key.json'
SHEET_NAME = 'construction_db'

# --- 台灣例假日 ---
HOLIDAYS = {
    "2025-01-01": "元旦", "2025-01-27": "小年夜", "2025-01-28": "除夕", "2025-01-29": "春節", "2025-01-30": "初二", "2025-01-31": "初三",
    "2025-02-28": "和平紀念日", "2025-04-04": "兒童節/清明節", "2025-05-01": "勞動節", "2025-05-31": "端午節",
    "2025-10-06": "中秋節", "2025-10-10": "國慶日",
    "2026-01-01": "元旦", "2026-02-16": "小年夜", "2026-02-17": "除夕", "2026-02-18": "春節",
    "2026-02-28": "和平紀念日", "2026-04-04": "兒童節", "2026-04-05": "清明節", "2026-05-01": "勞動節",
    "2026-06-19": "端午節", "2026-09-25": "中秋節", "2026-10-10": "國慶日"
}

# --- 預設資料結構 ---
DEFAULT_TEMPLATE = {
    "施工說明": ["正常施工", "暫停施工", "收尾階段", "驗收缺失改善", "天候不佳"],
    "相關紀錄": ["本日會議", "主管走動", "重要事件紀錄", "工安事項", "會勘紀錄"],
    "進料管理": ["鋼筋進場", "水泥進場", "磁磚進場", "設備進場", "其他材料"],
    "用料管理": ["混凝土 3000psi", "混凝土 2500psi", "CLSM", "級配", "水泥砂漿"],
    "工種 (人力)": ["粗工", "泥作", "水電", "油漆", "木工", "鐵工", "板模", "綁鐵", "打石", "清潔"],
    "機具 (設備)": ["挖土機 (怪手)", "山貓", "吊車", "發電機", "空壓機", "破碎機", "夯實機", "貨車"]
}

ORDER_MAP = {
    "施工說明": "01. 施工說明", "相關紀錄": "02. 相關紀錄", "進料管理": "03. 進料管理",
    "用料管理": "04. 用料管理", "工種 (人力)": "05. 工種 (人力)", "機具 (設備)": "06. 機具 (設備)"
}

DEFAULT_TYPES = {
    "施工說明": "text", "相關紀錄": "text", "進料管理": "text",
    "用料管理": "usage", "工種 (人力)": "cost", "機具 (設備)": "cost"
}

COST_CATEGORIES = [k for k, v in DEFAULT_TYPES.items() if v == 'cost']

# ==========================================
# 1. 🔐 登入驗證
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

def check_login():
    if st.session_state.password_input == SYSTEM_PASSWORD:
        st.session_state.logged_in = True
    else:
        st.error("❌ 密碼錯誤")

if not st.session_state.logged_in:
    st.markdown("## 🔒 系統鎖定")
    st.text_input("請輸入密碼：", type="password", key="password_input", on_change=check_login)
    st.stop()

# ==========================================
# 2. 核心邏輯
# ==========================================
@st.cache_resource
def get_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = None
    if os.path.exists(KEY_FILE):
        try: creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, scope)
        except: return None
    else:
        try:
            if "gcp_service_account" in st.secrets:
                creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
        except: return None
    if not creds: return None
    try:
        client = gspread.authorize(creds)
        return client.open(SHEET_NAME).sheet1
    except: return None

def get_date_info(date_obj):
    weekdays = ["(週一)", "(週二)", "(週三)", "(週四)", "(週五)", "(週六)", "(週日)"]
    date_str = date_obj.strftime("%Y-%m-%d")
    w_str = weekdays[date_obj.weekday()]
    if date_str in HOLIDAYS: return f"🔴 {w_str} ★{HOLIDAYS[date_str]}", True 
    if date_obj.weekday() >= 5: return f"🔴 {w_str}", True 
    return f"{w_str}", False

def load_json(filepath, default_data):
    if not os.path.exists(filepath):
        save_json(filepath, default_data)
        return default_data
    try:
        with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default_data

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)

def load_settings(): return load_json(SETTINGS_FILE, {"projects": ["預設專案"], "items": {"預設專案": copy.deepcopy(DEFAULT_TEMPLATE)}})
def save_settings(data): save_json(SETTINGS_FILE, data)
def load_prices(): return load_json(PRICES_FILE, {})
def save_prices(data): save_json(PRICES_FILE, data)

def load_data():
    cols = ['日期', '專案', '類別', '名稱', '單位', '數量', '單價', '總價', '備註', '月份']
    sheet = get_google_sheet()
    if not sheet: return pd.DataFrame(columns=cols)
    try:
        data = sheet.get_all_records()
        if not data: return pd.DataFrame(columns=cols)
        df = pd.DataFrame(data)
        df['日期'] = pd.to_datetime(df['日期'], errors='coerce').dt.date
        df['月份'] = pd.to_datetime(df['日期']).dt.strftime("%Y-%m")
        for col in ['總價', '數量', '單價']: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        return df
    except: return pd.DataFrame(columns=cols)

def save_dataframe(df):
    sheet = get_google_sheet()
    if not sheet: return
    df_save = df.drop(columns=[c for c in ['月份', '刪除', 'temp_month', '星期/節日', '🗓️ 星期/節日'] if c in df.columns])
    df_save['日期'] = df_save['日期'].astype(str)
    sheet.clear()
    sheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())

def append_data(date, project, category, name, unit, qty, price, note):
    total = qty * price if category in COST_CATEGORIES else 0
    row = [str(date), project, category, name, unit, qty, price, total, note]
    sheet = get_google_sheet()
    if sheet: sheet.append_row(row)

def update_by_scope(original_df, edited_part, proj, month, cats):
    original_df['temp_month'] = pd.to_datetime(original_df['日期']).dt.strftime("%Y-%m")
    mask = (original_df['temp_month'] == month) & (original_df['專案'] == proj) & (original_df['類別'].isin(cats))
    df_kept = original_df[~mask].copy()
    edited_clean = edited_part.drop(columns=[c for c in ['刪除', '星期/節日', '🗓️ 星期/節日'] if c in edited_part.columns])
    for col in ['數量', '單價']: edited_clean[col] = pd.to_numeric(edited_clean[col], errors='coerce').fillna(0)
    edited_clean['總價'] = edited_clean.apply(lambda r: r['數量']*r['單價'] if r['類別'] in COST_CATEGORIES else 0, axis=1)
    return pd.concat([df_kept, edited_clean], ignore_index=True)

def rename_project_logic(old_name, new_name, settings, prices):
    if new_name in settings["projects"]: return False, "名稱重複"
    idx = settings["projects"].index(old_name)
    settings["projects"][idx] = new_name
    settings["items"][new_name] = settings["items"].pop(old_name)
    if old_name in prices: prices[new_name] = prices.pop(old_name)
    save_prices(prices); save_settings(settings)
    df = load_data()
    if not df.empty:
        df.loc[df['專案'] == old_name, '專案'] = new_name
        save_dataframe(df)
    return True, "成功"

def rename_item_in_project(project, category, old_item, new_item, settings, prices):
    curr = settings["items"][project][category]
    if new_item in curr and old_item != new_item: return False
    curr[curr.index(old_item)] = new_item
    if project in prices and category in prices[project] and old_item in prices[project][category]:
        prices[project][category][new_item] = prices[project][category].pop(old_item)
        save_prices(prices)
    save_settings(settings)
    return True

def create_zip_backup():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        df = load_data()
        csv_buffer = io.StringIO()
        df.drop(columns=[c for c in ['月份', '刪除', 'temp_month', '星期/節日'] if c in df.columns]).to_csv(csv_buffer, index=False)
        zip_file.writestr(DATA_FILE, csv_buffer.getvalue())
        for file in [SETTINGS_FILE, PRICES_FILE, TYPES_FILE]:
            if os.path.exists(file): zip_file.write(file)
    buffer.seek(0)
    return buffer

# ==========================================
# 3. 初始化
# ==========================================
settings_data = load_settings()
category_types = load_json(TYPES_FILE, DEFAULT_TYPES)
price_data = load_prices()
df = load_data()

if 'mem_project' not in st.session_state: st.session_state.mem_project = settings_data["projects"][0]
if 'mem_date' not in st.session_state: st.session_state.mem_date = datetime.now()

# ==========================================
# 4. 主畫面
# ==========================================
st.title("🏗️ 多專案施工管理系統 (完美同步版)")

with st.sidebar:
    st.header("📅 日期與專案")
    proj_list = settings_data["projects"]
    if st.session_state.mem_project not in proj_list: st.session_state.mem_project = proj_list[0]
    global_project = st.selectbox("🏗️ 目前專案", proj_list, index=proj_list.index(st.session_state.mem_project))
    global_date = st.date_input("📅 工作日期", st.session_state.mem_date)
    day_str, is_red_day = get_date_info(global_date)
    st.markdown(f"### {global_date} {day_str}")
    st.session_state.mem_project = global_project
    st.session_state.mem_date = global_date
    current_items = settings_data["items"].get(global_project, copy.deepcopy(DEFAULT_TEMPLATE))
    st.divider()
    if st.button("🔄 強制重新整理"): st.cache_resource.clear(); st.rerun()
    if st.button("🔒 登出系統"): st.session_state.logged_in = False; st.rerun()

tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 快速日報輸入", "🛠️ 報表總覽與編輯修正", "📊 成本儀表板", "⚙️ 設定與管理"])

# --- TAB 1: 快速日報輸入 (維持原樣) ---
with tab_entry:
    st.info(f"正在填寫：**{global_project}** / **{global_date} {day_str}**")
    d_key = str(global_date)
    with st.expander("📝 01. 施工說明及相關紀錄", expanded=True):
        cols_g1 = st.columns(2)
        with cols_g1[0]: 
            real_cat = next((c for c in current_items if "施工" in c), None)
            if real_cat:
                st.markdown("**1. 施工說明**")
                with st.form(key=f"form_status_{d_key}"):
                    txt_item = st.selectbox("項目", current_items[real_cat])
                    txt_content = st.text_area("內容", height=100)
                    if st.form_submit_button("💾 儲存說明"):
                        append_data(global_date, global_project, real_cat, txt_item, "式", 1, 0, txt_content)
                        st.toast("已儲存"); time.sleep(1); st.rerun()
        with cols_g1[1]:
            real_cat = next((c for c in current_items if "紀錄" in c or "記錄" in c), None)
            if real_cat:
                st.markdown("**2. 相關紀錄**")
                with st.form(key=f"form_records_{d_key}"):
                    txt_item = st.selectbox("項目", current_items[real_cat])
                    txt_content = st.text_area("內容", height=100)
                    if st.form_submit_button("💾 儲存紀錄"):
                        append_data(global_date, global_project, real_cat, txt_item, "式", 1, 0, txt_content)
                        st.toast("已儲存"); time.sleep(1); st.rerun()
    # (省略部分重複代碼，確保邏輯與原版完全一致)
    with st.expander("🚛 02. 進料管理紀錄", expanded=True):
        real_cat = next((c for c in current_items if "進料" in c), None)
        if real_cat:
            cols_g2 = st.columns(3)
            for i in range(3):
                with cols_g2[i]:
                    with st.form(key=f"fi_{i}_{d_key}"):
                        in_it = st.selectbox("材料", current_items[real_cat], key=f"sel_i_{i}")
                        in_q = st.number_input("數量", min_value=0.0, step=1.0)
                        in_u = st.text_input("單位", value="式")
                        if st.form_submit_button(f"💾 儲存進料 {i+1}"):
                            append_data(global_date, global_project, real_cat, in_it, in_u, in_q, 0, "")
                            st.rerun()
    with st.expander("🧱 03. 用料管理紀錄", expanded=True):
        real_cat = next((c for c in current_items if "用料" in c), None)
        if real_cat:
            cols_g3 = st.columns(3)
            for i in range(3):
                with cols_g3[i]:
                    with st.form(key=f"fu_{i}_{d_key}"):
                        u_it = st.selectbox("材料", current_items[real_cat], key=f"sel_u_{i}")
                        u_q = st.number_input("數量", min_value=0.0, step=0.5)
                        u_u = st.text_input("單位", value="m3")
                        if st.form_submit_button(f"💾 儲存用料 {i+1}"):
                            append_data(global_date, global_project, real_cat, u_it, u_u, u_q, 0, "")
                            st.rerun()
    with st.expander("👷 04. 人力與機具出工紀錄", expanded=True):
        cols_g4 = st.columns(2)
        with cols_g4[0]:
            cat = next((c for c in current_items if "工種" in c), None)
            if cat:
                st.markdown("### 01. 工種 (人力)")
                it = st.selectbox("項目", current_items[cat])
                p_set = price_data.get(global_project, {}).get(cat, {}).get(it, {"price": 0, "unit": "工"})
                with st.form(key=f"fm_{d_key}"):
                    cq, cp = st.columns(2)
                    q = cq.number_input("數量", value=1.0, step=0.5)
                    p = cp.number_input("單價", value=float(p_set["price"]))
                    u = st.text_input("單位", value=p_set["unit"])
                    if st.form_submit_button("💾 新增工種"):
                        append_data(global_date, global_project, cat, it, u, q, p, "")
                        st.rerun()
        with cols_g4[1]:
            cat = next((c for c in current_items if "機具" in c), None)
            if cat:
                st.markdown("### 02. 機具 (設備)")
                it = st.selectbox("項目", current_items[cat])
                p_set = price_data.get(global_project, {}).get(cat, {}).get(it, {"price": 0, "unit": "式"})
                with st.form(key=f"fe_{d_key}"):
                    cq, cp = st.columns(2)
                    q = cq.number_input("數量", value=1.0, step=0.5)
                    p = cp.number_input("單價", value=float(p_set["price"]))
                    u = st.text_input("單位", value=p_set["unit"])
                    if st.form_submit_button("💾 新增機具"):
                        append_data(global_date, global_project, cat, it, u, q, p, "")
                        st.rerun()

# --- TAB 2: 報表總覽 (維持原樣) ---
with tab_data:
    st.subheader("🛠️ 報表編輯與檢視")
    proj_df = df[df['專案'] == global_project].copy()
    if proj_df.empty: st.info("無資料")
    else:
        m_list = sorted(proj_df['月份'].unique().tolist(), reverse=True)
        sel_m = st.selectbox("編輯月份", m_list)
        m_df = proj_df[proj_df['月份'] == sel_m].copy()
        for base_key, display_name in ORDER_MAP.items():
            target_cats = [c for c in current_items if base_key in c]
            if target_cats:
                sec_df = m_df[m_df['類別'].isin(target_cats)].copy()
                if not sec_df.empty:
                    st.subheader(display_name)
                    if '刪除' not in sec_df.columns: sec_df.insert(0, "刪除", False)
                    edited = st.data_editor(sec_df, key=f"ed_{base_key}", use_container_width=True, hide_index=True)
                    if st.button("💾 更新修改", key=f"btn_{base_key}"):
                        save_dataframe(update_by_scope(df, edited[~edited['刪除']], global_project, sel_m, target_cats))
                        st.toast("更新成功"); time.sleep(1); st.rerun()

# --- TAB 3: 成本儀表板 (新增年份篩選) ---
with tab_dash:
    if df.empty: st.info("無資料")
    else:
        dash_df = df[df['專案'] == global_project].copy()
        dash_df['年份'] = pd.to_datetime(dash_df['日期']).dt.year
        y_list = sorted(dash_df['年份'].unique().tolist(), reverse=True)
        sel_y = st.selectbox("📅 選擇統計年份", y_list)
        year_df = dash_df[dash_df['年份'] == sel_y]
        
        k1, k2, k3 = st.columns(3)
        k1.metric(f"{sel_y} 年度費用", f"${year_df['總價'].sum():,.0f}")
        k2.metric("專案總費用", f"${dash_df['總價'].sum():,.0f}")
        
        cost_df = year_df[year_df['總價'] > 0]
        if not cost_df.empty:
            m_list = sorted(cost_df['月份'].unique().tolist(), reverse=True)
            sel_m = st.selectbox("圖表統計月份", m_list)
            m_data = cost_df[cost_df['月份'] == sel_m]
            st.altair_chart(alt.Chart(m_data).mark_arc(innerRadius=50).encode(theta="sum(總價)", color="類別"), use_container_width=True)

# --- TAB 4: ⚙️ 設定與管理 (恢復原本三欄選單 + 備份置頂) ---
with tab_settings:
    st.header("⚙️ 設定與管理")
    
    # 🌟 1. 置頂：資料備份中心
    with st.expander("📦 資料備份中心 (置頂)", expanded=True):
        st.info("下載備份 (含雲端資料與本地設定)")
        st.download_button("📦 下載完整系統備份 (ZIP)", create_zip_backup(), file_name=f"full_backup_{datetime.now().strftime('%Y%m%d')}.zip", mime="application/zip")
        st.divider()
        up_file = st.file_uploader("📤 系統還原 (CSV 或 ZIP)", type=['csv', 'zip'])
        if up_file and st.button("⚠️ 確認還原"):
            try:
                if up_file.name.endswith('.csv'):
                    save_dataframe(pd.read_csv(up_file))
                elif up_file.name.endswith('.zip'):
                    with zipfile.ZipFile(up_file, 'r') as z: z.extractall(".")
                st.success("還原成功！"); time.sleep(1); st.rerun()
            except: st.error("還原失敗")

    # 2. 專案管理
    with st.expander("1. 專案管理", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            with st.form("add_p"):
                new_p = st.text_input("新增專案")
                if st.form_submit_button("➕ 新增"):
                    if new_p and new_p not in settings_data["projects"]:
                        settings_data["projects"].append(new_p)
                        settings_data["items"][new_p] = copy.deepcopy(DEFAULT_TEMPLATE)
                        save_settings(settings_data); st.rerun()
        with c2:
            ren_p = st.text_input("修改名稱為", value=global_project)
            if st.button("✏️ 確認改名"):
                rename_project_logic(global_project, ren_p, settings_data, price_data); st.rerun()
        with c3:
            if st.button("🗑️ 移除此專案", type="primary"):
                if len(settings_data["projects"]) > 1:
                    settings_data["projects"].remove(global_project)
                    save_settings(settings_data); st.rerun()

    # 3. 匯入/複製 專案設定
    with st.expander("2. 匯入/複製 專案設定 (範本)", expanded=False):
        src_opts = ["(系統預設範本)"] + [p for p in settings_data["projects"] if p != global_project]
        src_p = st.selectbox("選擇來源", src_opts)
        if st.button("📥 確認匯入"):
            settings_data["items"][global_project] = copy.deepcopy(DEFAULT_TEMPLATE if src_p == "(系統預設範本)" else settings_data["items"][src_p])
            save_settings(settings_data); st.success("匯入成功"); st.rerun()

    # 🌟 4. 恢復三欄式細項管理
    st.subheader("3. 獨立選單與預設單價/單位")
    p_items = settings_data["items"][global_project]
    if global_project not in price_data: price_data[global_project] = {}
    
    col_s1, col_s2, col_s3 = st.columns(3)
    for i, (cat, display_name) in enumerate(ORDER_MAP.items()):
        target_cat = next((k for k in p_items.keys() if cat in k), None)
        if target_cat:
            col = [col_s1, col_s2, col_s3][i % 3]
            with col:
                st.info(f"📁 {display_name}")
                with st.expander("展開編輯"):
                    # 新增細項
                    with st.form(f"add_it_{target_cat}"):
                        ni = st.text_input("新增細項")
                        if st.form_submit_button("加入"):
                            p_items[target_cat].append(ni); save_settings(settings_data); st.rerun()
                    
                    # 預設單價 (費用類別)
                    if target_cat in COST_CATEGORIES:
                        for item in p_items[target_cat]:
                            p_info = price_data[global_project].get(target_cat, {}).get(item, {"price": 0, "unit": "工" if "工種" in target_cat else "式"})
                            c_p, c_u, c_b = st.columns([2, 1, 1])
                            new_p = c_p.number_input(f"{item} 單價", value=float(p_info["price"]), key=f"p_{target_cat}_{item}")
                            new_u = c_u.text_input(f"單位", value=p_info["unit"], key=f"u_{target_cat}_{item}")
                            if c_b.button("✅", key=f"s_{target_cat}_{item}"):
                                if target_cat not in price_data[global_project]: price_data[global_project][target_cat] = {}
                                price_data[global_project][target_cat][item] = {"price": new_p, "unit": new_u}
                                save_prices(price_data); st.toast("已儲存")
                    
                    # 移除項目
                    tgt = st.selectbox("選擇項目", p_items[target_cat], key=f"sel_{target_cat}")
                    if st.button("🗑️ 移除選中項", key=f"del_{target_cat}"):
                        p_items[target_cat].remove(tgt); save_settings(settings_data); st.rerun()