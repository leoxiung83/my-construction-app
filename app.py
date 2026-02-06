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
        with open(filepath, 'w', encoding='utf-8') as f: json.dump(default_data, f, ensure_ascii=False, indent=4)
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

def append_data(date, project, category, name, unit, qty, price, note, cost_cats):
    total = qty * price if category in cost_cats else 0
    row = [str(date), project, category, name, unit, qty, price, total, note]
    sheet = get_google_sheet()
    if sheet: sheet.append_row(row)

def update_by_scope(original_df, edited_part, proj, month, cats, cost_cats):
    original_df['temp_month'] = pd.to_datetime(original_df['日期']).dt.strftime("%Y-%m")
    mask = (original_df['temp_month'] == month) & (original_df['專案'] == proj) & (original_df['類別'].isin(cats))
    df_kept = original_df[~mask].copy()
    edited_clean = edited_part.drop(columns=[c for c in ['刪除', '星期/節日', '🗓️ 星期/節日'] if c in edited_part.columns])
    for col in ['數量', '單價']: edited_clean[col] = pd.to_numeric(edited_clean[col], errors='coerce').fillna(0)
    edited_clean['總價'] = edited_clean.apply(lambda r: r['數量']*r['單價'] if r['類別'] in cost_cats else 0, axis=1)
    return pd.concat([df_kept, edited_clean], ignore_index=True)

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
        df.to_csv(csv_buffer, index=False)
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
COST_CATEGORIES = [k for k, v in category_types.items() if v == 'cost']
df = load_data()

if 'mem_project' not in st.session_state: st.session_state.mem_project = settings_data["projects"][0]
if 'mem_date' not in st.session_state: st.session_state.mem_date = datetime.now()

# ==========================================
# 4. 主畫面
# ==========================================
st.title("🏗️ 多專案施工管理系統")

with st.sidebar:
    proj_list = settings_data["projects"]
    if st.session_state.mem_project not in proj_list: st.session_state.mem_project = proj_list[0]
    global_project = st.selectbox("🏗️ 目前專案", proj_list, index=proj_list.index(st.session_state.mem_project))
    global_date = st.date_input("📅 工作日期", st.session_state.mem_date)
    day_str, is_red_day = get_date_info(global_date)
    st.markdown(f"### {global_date} {day_str}")
    st.session_state.mem_project = global_project
    st.session_state.mem_date = global_date
    current_items = settings_data["items"].get(global_project, copy.deepcopy(DEFAULT_TEMPLATE))
    if st.button("🔄 重新整理"): st.cache_resource.clear(); st.rerun()

tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 快速日報輸入", "🛠️ 報表總覽與編輯修正", "📊 成本儀表板", "⚙️ 設定與管理"])

# --- TAB 1: 快速日報輸入 ---
with tab_entry:
    st.info(f"填寫中：{global_project} / {global_date}")
    d_key = str(global_date)
    
    # 紀錄已處理過的類別，避免重複顯示
    handled_cats = []

    # 01. 施工說明及紀錄 (固定區塊)
    with st.expander("📝 01. 施工說明及相關紀錄", expanded=True):
        c1, c2 = st.columns(2)
        for i, keyword in enumerate(["施工", "紀錄"]):
            cat = next((k for k in current_items if keyword in k), None)
            if cat:
                handled_cats.append(cat)
                with [c1, c2][i]:
                    st.markdown(f"**{cat}**")
                    with st.form(key=f"f_{cat}_{d_key}"):
                        it = st.selectbox("項目", current_items[cat])
                        tx = st.text_area("內容", height=100)
                        if st.form_submit_button("儲存"):
                            append_data(global_date, global_project, cat, it, "式", 1, 0, tx, COST_CATEGORIES)
                            st.toast("已儲存"); time.sleep(0.5); st.rerun()

    # 02 & 03. 進料與用料 (固定區塊)
    with st.expander("🚛 02. 進料與用料管理", expanded=True):
        c1, c2 = st.columns(2)
        for i, keyword in enumerate(["進料", "用料"]):
            cat = next((k for k in current_items if keyword in k), None)
            if cat:
                handled_cats.append(cat)
                with [c1, c2][i]:
                    st.markdown(f"**{cat}**")
                    with st.form(key=f"f_{cat}_{d_key}"):
                        it = st.selectbox("項目", current_items[cat])
                        q = st.number_input("數量", min_value=0.0, step=0.1)
                        u = st.text_input("單位", value="m3" if "用料" in cat else "式")
                        if st.form_submit_button("儲存"):
                            append_data(global_date, global_project, cat, it, u, q, 0, "", COST_CATEGORIES)
                            st.toast("已儲存"); time.sleep(0.5); st.rerun()

    # 04. 人力與機具 (固定區塊)
    with st.expander("👷 03. 人力與機具出工", expanded=True):
        c1, c2 = st.columns(2)
        for i, keyword in enumerate(["工種", "機具"]):
            cat = next((k for k in current_items if keyword in k), None)
            if cat:
                handled_cats.append(cat)
                with [c1, c2][i]:
                    st.markdown(f"**{cat}**")
                    it = st.selectbox("項目", current_items[cat], key=f"s_{cat}")
                    p_info = price_data.get(global_project, {}).get(cat, {}).get(it, {"price": 0, "unit": "工" if "工種" in cat else "式"})
                    with st.form(key=f"f_{cat}_{d_key}"):
                        col_q, col_p = st.columns(2)
                        qty = col_q.number_input("數量", value=1.0, step=0.5)
                        pri = col_p.number_input("單價", value=float(p_info["price"]))
                        uni = st.text_input("單位", value=p_info["unit"])
                        if st.form_submit_button("新增紀錄"):
                            append_data(global_date, global_project, cat, it, uni, qty, pri, "", COST_CATEGORIES)
                            st.toast("已儲存"); time.sleep(0.5); st.rerun()

    # 🌟 動態新增欄位 (對應自定義類別)
    remaining_cats = [c for c in current_items.keys() if c not in handled_cats]
    if remaining_cats:
        with st.expander("✨ 其他自定義管理項目", expanded=True):
            for cat in remaining_cats:
                st.markdown(f"--- \n#### {cat}")
                c_type = category_types.get(cat, "text")
                with st.form(key=f"dyn_{cat}_{d_key}"):
                    it = st.selectbox("項目", current_items[cat])
                    if c_type == "text":
                        tx = st.text_area("內容")
                        if st.form_submit_button(f"儲存 {cat}"):
                            append_data(global_date, global_project, cat, it, "式", 1, 0, tx, COST_CATEGORIES)
                            st.rerun()
                    elif c_type == "cost":
                        p_info = price_data.get(global_project, {}).get(cat, {}).get(it, {"price": 0, "unit": "式"})
                        q_col, p_col = st.columns(2)
                        q = q_col.number_input("數量", value=1.0)
                        p = p_col.number_input("單價", value=float(p_info["price"]))
                        u = st.text_input("單位", value=p_info["unit"])
                        if st.form_submit_button(f"儲存 {cat}"):
                            append_data(global_date, global_project, cat, it, u, q, p, "", COST_CATEGORIES)
                            st.rerun()
                    else: # usage
                        q = st.number_input("數量", value=0.0)
                        u = st.text_input("單位", value="式")
                        if st.form_submit_button(f"儲存 {cat}"):
                            append_data(global_date, global_project, cat, it, u, q, 0, "", COST_CATEGORIES)
                            st.rerun()

# --- TAB 2: 報表總覽與編輯 ---
with tab_data:
    st.subheader("🛠️ 報表編輯與檢視")
    proj_df = df[df['專案'] == global_project].copy()
    if proj_df.empty: st.info("無資料")
    else:
        m_list = sorted(proj_df['月份'].unique().tolist(), reverse=True)
        sel_m = st.selectbox("月份", m_list)
        m_df = proj_df[proj_df['月份'] == sel_m].copy()
        for cat in current_items.keys():
            sec_df = m_df[m_df['類別'] == cat].copy()
            if not sec_df.empty:
                st.markdown(f"**{cat}**")
                if '刪除' not in sec_df.columns: sec_df.insert(0, "刪除", False)
                edited = st.data_editor(sec_df, key=f"ed_{cat}", hide_index=True)
                if st.button("更新", key=f"btn_{cat}"):
                    final = update_by_scope(df, edited[~edited['刪除']], global_project, sel_m, [cat], COST_CATEGORIES)
                    save_dataframe(final); st.rerun()

# --- TAB 3: 成本儀表板 (新增年份選擇) ---
with tab_dash:
    if df.empty: st.info("無資料")
    else:
        dash_df = df[df['專案'] == global_project].copy()
        dash_df['年份'] = pd.to_datetime(dash_df['日期']).dt.year
        
        # 🌟 年份篩選選單
        y_list = sorted(dash_df['年份'].unique().tolist(), reverse=True)
        sel_y = st.selectbox("📅 選擇統計年份", y_list)
        year_df = dash_df[dash_df['年份'] == sel_y]

        k1, k2 = st.columns(2)
        k1.metric(f"{sel_y} 年度總費用", f"${year_df['總價'].sum():,.0f}")
        k2.metric("不限年份總計", f"${dash_df['總價'].sum():,.0f}")
        
        cost_df = year_df[year_df['總價'] > 0]
        if not cost_df.empty:
            m_list = sorted(cost_df['月份'].unique().tolist(), reverse=True)
            sel_m = st.selectbox("月份統計", m_list)
            m_data = cost_df[cost_df['月份'] == sel_m]
            pie = alt.Chart(m_data).mark_arc(innerRadius=50).encode(theta="sum(總價)", color="類別", tooltip=["類別", "sum(總價)"])
            st.altair_chart(pie, use_container_width=True)

# --- TAB 4: 設定與管理 (功能置頂) ---
with tab_settings:
    # 🌟 2. 管理備份 (移動到最上層)
    with st.expander("📦 系統備份與還原", expanded=True):
        st.write("建議定期備份以確保資料安全。")
        st.download_button("📥 下載完整備份 (ZIP)", create_zip_backup(), file_name=f"backup_{datetime.now().date()}.zip")
        st.divider()
        up = st.file_uploader("還原資料 (CSV/ZIP)")
        if up and st.button("確認還原"):
            try:
                new_df = pd.read_csv(up); save_dataframe(new_df)
                st.success("還原成功"); st.rerun()
            except: st.error("格式不符")

    # 1. 專案與類別管理
    st.subheader("➕ 新增管理項目")
    with st.container(border=True):
        sc1, sc2, sc3 = st.columns([4, 3, 1])
        with sc1: n_cat = st.text_input("標題名稱 (如: 05.安全檢查)")
        with sc2: n_type = st.selectbox("類型", ["text", "usage", "cost"])
        with sc3:
            st.write("")
            if st.button("新增"):
                if n_cat:
                    settings_data["items"][global_project][n_cat] = []
                    category_types[n_cat] = n_type
                    save_json(TYPES_FILE, category_types); save_settings(settings_data); st.rerun()

    st.divider()
    # 項目明細管理
    cat_to_edit = st.selectbox("選擇管理類別", list(current_items.keys()))
    if cat_to_edit:
        st.write(f"管理：**{cat_to_edit}**")
        with st.form(f"add_{cat_to_edit}"):
            ni = st.text_input("新增細項名稱")
            if st.form_submit_button("加入"):
                current_items[cat_to_edit].append(ni); save_settings(settings_data); st.rerun()
        
        # 列表顯示
        c_type = category_types.get(cat_to_edit, "text")
        for idx, item in enumerate(current_items[cat_to_edit]):
            cols = st.columns([3, 2, 2, 1])
            cols[0].write(f"`{item}`")
            if c_type == "cost":
                p_info = price_data.get(global_project, {}).get(cat_to_edit, {}).get(item, {"price": 0, "unit": "式"})
                np = cols[1].number_input("預設單價", value=float(p_info["price"]), key=f"p_{idx}")
                nu = cols[2].text_input("預設單位", value=p_info["unit"], key=f"u_{idx}")
                if cols[3].button("💾", key=f"s_{idx}"):
                    if global_project not in price_data: price_data[global_project] = {}
                    if cat_to_edit not in price_data[global_project]: price_data[global_project][cat_to_edit] = {}
                    price_data[global_project][cat_to_edit][item] = {"price": np, "unit": nu}
                    save_prices(price_data); st.toast("已儲存")