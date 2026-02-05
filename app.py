import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import json
import time
import io
import re
from datetime import datetime
from PIL import Image
import requests

# ==========================================
# 0. 系統設定
# ==========================================
st.set_page_config(page_title="多專案施工管理系統 (線上動態版)", layout="wide", page_icon="☁️")

# --- 🔒 雲端設定 ---
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# 請確保您的 secrets.toml 有設定好
def get_creds():
    if "gcp_service_account" in st.secrets:
        return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=SCOPES)
    return None

IMAGE_FOLDER_ID = st.secrets.get("IMAGE_FOLDER_ID", "")
SHEET_NAME = "施工管理系統資料庫"

# --- 預設值 ---
DEFAULT_CAT_CONFIG = [
    {"key": "施工說明", "display": "01. 施工說明", "type": "text"},
    {"key": "相關紀錄", "display": "02. 相關紀錄", "type": "text"},
    {"key": "進料管理", "display": "03. 進料管理", "type": "text"},
    {"key": "用料管理", "display": "04. 用料管理", "type": "usage"},
    {"key": "工種 (人力)", "display": "05. 工種 (人力)", "type": "cost"},
    {"key": "機具 (設備)", "display": "06. 機具 (設備)", "type": "cost"}
]

DEFAULT_ITEMS = {
    "施工說明": ["正常施工", "暫停施工", "收尾階段", "驗收缺失改善", "天候不佳"],
    "相關紀錄": ["本日會議", "主管走動", "重要事件紀錄", "工安事項", "會勘紀錄"],
    "進料管理": ["鋼筋進場", "水泥進場", "磁磚進場", "設備進場", "其他材料"],
    "用料管理": ["混凝土 3000psi", "混凝土 2500psi", "CLSM", "級配", "水泥砂漿"],
    "工種 (人力)": ["粗工", "泥作", "水電", "油漆", "木工", "鐵工", "板模", "綁鐵", "打石", "清潔"],
    "機具 (設備)": ["挖土機 (怪手)", "山貓", "吊車", "發電機", "空壓機", "破碎機", "夯實機", "貨車"]
}

# ---------------------------------------------------------
# 1. 全域工具函式 (移到最上方以避免 NameError)
# ---------------------------------------------------------
def get_date_info(date_obj):
    weekdays = ["(週一)", "(週二)", "(週三)", "(週四)", "(週五)", "(週六)", "(週日)"]
    w_str = weekdays[date_obj.weekday()]
    return f"{w_str}"

def extract_image_from_note(note_str):
    if not note_str: return None
    # 針對線上版可能的 NaN 處理
    if pd.isna(note_str): return None
    match = re.search(r'\(圖:(.*?)\)', str(note_str))
    if match: return match.group(1).strip()
    return None

def remove_image_tag(note_str):
    if not note_str: return ""
    if pd.isna(note_str): return ""
    return re.sub(r'\(圖:.*?\)', '', str(note_str)).strip()

def get_drive_service():
    creds = get_creds()
    if creds: return build('drive', 'v3', credentials=creds)
    return None

def upload_image_to_drive(image_file, filename):
    service = get_drive_service()
    if not service: return None
    try:
        if not IMAGE_FOLDER_ID:
            st.error("尚未設定 IMAGE_FOLDER_ID")
            return None
        file_metadata = {'name': filename, 'parents': [IMAGE_FOLDER_ID]}
        media = MediaIoBaseUpload(image_file, mimetype=image_file.type)
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        return file.get('webViewLink')
    except Exception as e:
        st.error(f"上傳錯誤: {e}")
        return None

# ---------------------------------------------------------
# 2. Google Sheets 讀寫邏輯
# ---------------------------------------------------------
def get_google_sheet_client():
    creds = get_creds()
    if creds: return gspread.authorize(creds)
    return None

def load_settings_online():
    client = get_google_sheet_client()
    if not client: return None
    try:
        sh = client.open(SHEET_NAME)
        try:
            ws = sh.worksheet("System_Config")
            json_str = ws.acell('A1').value
            if not json_str: raise ValueError
            data = json.loads(json_str)
            if "cat_config" not in data: # 舊版相容
                data["cat_config"] = DEFAULT_CAT_CONFIG
            # 確保 items 結構完整
            for proj in data["projects"]:
                if proj not in data["items"]: data["items"][proj] = {}
                for cat in data["cat_config"]:
                    if cat["key"] not in data["items"][proj]:
                        data["items"][proj][cat["key"]] = []
            return data
        except:
            return reset_settings_online(sh)
    except Exception as e:
        st.error(f"設定讀取錯誤: {e}")
        return None

def save_settings_online(data):
    client = get_google_sheet_client()
    try:
        sh = client.open(SHEET_NAME)
        try: ws = sh.worksheet("System_Config")
        except: ws = sh.add_worksheet("System_Config", 10, 10)
        ws.update_acell('A1', json.dumps(data, ensure_ascii=False))
    except Exception as e:
        st.error(f"設定儲存失敗: {e}")

def reset_settings_online(sh):
    default_data = {
        "projects": ["預設專案"],
        "items": {"預設專案": DEFAULT_ITEMS},
        "cat_config": DEFAULT_CAT_CONFIG,
        "prices": {} # 將價格也存入設定檔
    }
    try: ws = sh.add_worksheet("System_Config", 10, 10)
    except: ws = sh.worksheet("System_Config")
    ws.update_acell('A1', json.dumps(default_data, ensure_ascii=False))
    return default_data

def load_data_online():
    client = get_google_sheet_client()
    try:
        sh = client.open(SHEET_NAME)
        ws = sh.sheet1
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        cols = ['日期', '專案', '類別', '名稱', '單位', '數量', '單價', '總價', '備註', '月份']
        if df.empty: return pd.DataFrame(columns=cols)
        for c in cols:
            if c not in df.columns: df[c] = ""
        
        # 確保日期格式
        df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
        df = df.dropna(subset=['日期'])
        df['日期'] = df['日期'].dt.date
        df['月份'] = pd.to_datetime(df['日期']).dt.strftime("%Y-%m")
        for col in ['總價', '數量', '單價']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        return df
    except: return pd.DataFrame()

def append_data_online(row_list):
    client = get_google_sheet_client()
    try:
        sh = client.open(SHEET_NAME)
        ws = sh.sheet1
        ws.append_row(row_list)
    except Exception as e: st.error(f"寫入失敗: {e}")

def update_sheet_data_online(df):
    client = get_google_sheet_client()
    try:
        sh = client.open(SHEET_NAME)
        ws = sh.sheet1
        df_save = df.copy()
        df_save['日期'] = df_save['日期'].astype(str)
        cols_drop = ['月份', 'temp_month', '星期/節日']
        df_save = df_save.drop(columns=[c for c in cols_drop if c in df_save.columns])
        ws.clear()
        ws.update([df_save.columns.values.tolist()] + df_save.values.tolist())
    except Exception as e: st.error(f"更新失敗: {e}")

# --- 管理功能邏輯 ---
def update_item_name(project, category, old_name, new_name, settings, prices):
    if old_name == new_name: return False
    curr_list = settings["items"][project].get(category, [])
    if new_name in curr_list: return False
    if old_name in curr_list:
        curr_list[curr_list.index(old_name)] = new_name
    
    # 同步價格 key
    if project in prices and category in prices[project] and old_name in prices[project][category]:
        prices[project][category][new_name] = prices[project][category].pop(old_name)
        settings["prices"] = prices # 更新到大設定檔
    
    # 同步歷史資料
    df = load_data_online()
    if not df.empty:
        mask = (df['專案']==project) & (df['類別']==category) & (df['名稱']==old_name)
        if mask.any():
            df.loc[mask, '名稱'] = new_name
            update_sheet_data_online(df)
            
    save_settings_online(settings)
    return True

def update_category_config(idx, new_display, settings):
    settings["cat_config"][idx]["display"] = new_display
    save_settings_online(settings)
    return True

def add_new_category_block(new_key, new_display, new_type, settings):
    for cat in settings["cat_config"]:
        if cat["key"] == new_key: return False
    settings["cat_config"].append({"key": new_key, "display": new_display, "type": new_type})
    for proj in settings["items"]:
        if new_key not in settings["items"][proj]:
            settings["items"][proj][new_key] = []
    save_settings_online(settings)
    return True

# --- 初始化 ---
settings_data = load_settings_online()
if not settings_data: st.stop()

# 價格資料現在存在 settings_data["prices"] 中
price_data = settings_data.get("prices", {})
df = load_data_online()

CAT_CONFIG_LIST = settings_data["cat_config"]
CAT_TYPE_MAP = {c["key"]: c["type"] for c in CAT_CONFIG_LIST}

if 'mem_project' not in st.session_state:
    st.session_state.mem_project = settings_data["projects"][0] if settings_data["projects"] else "預設專案"
if 'mem_date' not in st.session_state:
    st.session_state.mem_date = datetime.now()
if 'last_check_date' not in st.session_state:
    st.session_state.last_check_date = st.session_state.mem_date

# ==========================================
# 主介面
# ==========================================
st.title("🏗️ 多專案施工管理系統 (線上動態版)")

with st.sidebar:
    st.header("📅 日期與專案")
    proj_list = settings_data["projects"]
    if st.session_state.mem_project not in proj_list:
        st.session_state.mem_project = proj_list[0]
    idx_proj = proj_list.index(st.session_state.mem_project)
    global_project = st.selectbox("🏗️ 目前專案", proj_list, index=idx_proj, key="global_proj")
    global_date = st.date_input("📅 工作日期", st.session_state.mem_date, key="global_date")
    
    if global_date != st.session_state.last_check_date:
        st.session_state.last_check_date = global_date
    
    day_str = get_date_info(global_date)
    st.markdown(f"### {global_date} {day_str}")
    
    st.session_state.mem_project = global_project
    st.session_state.mem_date = global_date
    
    if global_project not in settings_data["items"]:
        settings_data["items"][global_project] = {}
    current_items = settings_data["items"][global_project]

tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 快速日報輸入", "🛠️ 報表總覽與編輯修正", "📊 成本儀表板", "⚙️ 設定與管理"])

# === Tab 1: 快速日報輸入 (讀取雲端設定動態生成) ===
with tab_entry:
    st.info(f"正在填寫：**{global_project}** / **{global_date} {day_str}**")
    d_key = str(global_date)
    configs = CAT_CONFIG_LIST
    
    def process_append(cat_key, cat_type, name, unit, qty, price, note, img_file):
        img_url = None
        if img_file:
            with st.spinner("📸 照片上傳中..."):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = f"{timestamp}_{global_project}_{cat_key}.jpg"
                img_url = upload_image_to_drive(img_file, fname)
        final_note = f"{note} (圖:{img_url})" if img_url else note
        total = qty * price if cat_type == 'cost' else 0
        row = [str(global_date), global_project, cat_key, name, unit, qty, price, total, final_note, str(global_date)[:7]]
        append_data_online(row)
        st.toast("✅ 資料已儲存！")
        time.sleep(1)

    # 動態區塊生成
    # 為了保持您要求的版面：前六個固定配置，後面新增的往下排
    
    # 1. 施工說明 & 相關紀錄 (左右)
    if len(configs) > 1:
        with st.expander(f"📝 {configs[0]['display']} 及 {configs[1]['display']}", expanded=True):
            cols = st.columns(2)
            for i in range(2):
                conf = configs[i]
                with cols[i]:
                    st.markdown(f"**{conf['display']}**")
                    with st.form(key=f"f_{i}_{d_key}"):
                        opts = current_items.get(conf["key"], [])
                        it = st.selectbox("項目", opts if opts else ["(請新增)"], key=f"s_{i}")
                        ct = st.text_area("內容", height=100, key=f"c_{i}")
                        im = st.file_uploader("照", type=['jpg','png'], key=f"m_{i}")
                        if st.form_submit_button("💾 儲存"):
                            process_append(conf["key"], conf["type"], it, "式", 1, 0, ct, im)

    # 2. 進料 (3欄)
    if len(configs) > 2:
        conf = configs[2]
        with st.expander(f"🚛 {conf['display']}", expanded=True):
            cols = st.columns(3)
            for k in range(3):
                with cols[k]:
                    st.markdown(f"**{conf['display']} {k+1}**")
                    with st.form(key=f"f_{2}_{k}_{d_key}"):
                        opts = current_items.get(conf["key"], [])
                        it = st.selectbox("材料", opts if opts else ["(請新增)"], key=f"s_{2}_{k}")
                        c1, c2 = st.columns(2)
                        qt = c1.number_input("數量", 0.0, step=1.0, key=f"q_{2}_{k}")
                        un = c2.text_input("單位", "式", key=f"u_{2}_{k}")
                        nt = st.text_input("備註", key=f"n_{2}_{k}")
                        im = st.file_uploader("照", type=['jpg','png'], key=f"m_{2}_{k}")
                        if st.form_submit_button("💾"):
                            process_append(conf["key"], conf["type"], it, un, qt, 0, nt, im)

    # 3. 用料 (3欄)
    if len(configs) > 3:
        conf = configs[3]
        with st.expander(f"🧱 {conf['display']}", expanded=True):
            cols = st.columns(3)
            for k in range(3):
                with cols[k]:
                    st.markdown(f"**{conf['display']} {k+1}**")
                    with st.form(key=f"f_{3}_{k}_{d_key}"):
                        opts = current_items.get(conf["key"], [])
                        it = st.selectbox("材料", opts if opts else ["(請新增)"], key=f"s_{3}_{k}")
                        c1, c2 = st.columns(2)
                        qt = c1.number_input("數量", 0.0, step=0.5, key=f"q_{3}_{k}")
                        un = c2.text_input("單位", "m3", key=f"u_{3}_{k}")
                        nt = st.text_input("備註", key=f"n_{3}_{k}")
                        if st.form_submit_button("💾"):
                            process_append(conf["key"], conf["type"], it, un, qt, 0, nt, None)

    # 4. 人力 & 機具 (左右)
    if len(configs) > 5:
        with st.expander("👷 人力與機具出工紀錄", expanded=True):
            cols = st.columns(2)
            # 人力
            with cols[0]:
                conf = configs[4]
                st.markdown(f"### {conf['display']}")
                opts = current_items.get(conf["key"], [])
                prices = price_data.get(global_project, {}).get(conf["key"], {})
                it = st.selectbox("項目", opts if opts else ["(請新增)"], key=f"s_4")
                def_p = float(prices.get(it, {}).get("price", 0))
                def_u = prices.get(it, {}).get("unit", "工")
                c1, c2 = st.columns(2)
                qt = c1.number_input("數量", 0.0, step=0.5, key=f"q_4")
                pr = c2.number_input("單價", value=def_p, step=100.0, key=f"p_4")
                un = st.text_input("單位", value=def_u, key=f"u_4")
                nt = st.text_input("備註", key=f"n_4")
                if st.button(f"💾 新增{conf['display']}", key="b_4"):
                    process_append(conf["key"], conf["type"], it, un, qt, pr, nt, None)
                    st.rerun()
            # 機具
            with cols[1]:
                conf = configs[5]
                st.markdown(f"### {conf['display']}")
                opts = current_items.get(conf["key"], [])
                prices = price_data.get(global_project, {}).get(conf["key"], {})
                it = st.selectbox("項目", opts if opts else ["(請新增)"], key=f"s_5")
                def_p = float(prices.get(it, {}).get("price", 0))
                def_u = prices.get(it, {}).get("unit", "式")
                c1, c2 = st.columns(2)
                qt = c1.number_input("數量", 0.0, step=0.5, key=f"q_5")
                pr = c2.number_input("單價", value=def_p, step=100.0, key=f"p_5")
                un = st.text_input("單位", value=def_u, key=f"u_5")
                nt = st.text_input("備註", key=f"n_5")
                if st.button(f"💾 新增{conf['display']}", key="b_5"):
                    process_append(conf["key"], conf["type"], it, un, qt, pr, nt, None)
                    st.rerun()

    # 5. 其他新增區塊
    if len(configs) > 6:
        st.divider()
        st.markdown("#### ➕ 其他自訂區塊")
        for i in range(6, len(configs)):
            conf = configs[i]
            with st.expander(f"📝 {conf['display']}", expanded=True):
                with st.form(key=f"f_{i}_{d_key}"):
                    opts = current_items.get(conf["key"], [])
                    it = st.selectbox("項目", opts if opts else ["(請新增)"], key=f"s_{i}")
                    c1, c2 = st.columns([1, 2])
                    if conf["type"] == 'text':
                        nt = c2.text_area("內容", height=68, key=f"c_{i}")
                        qt, pr, un = 1, 0, "式"
                    else:
                        nt = c2.text_input("備註", key=f"n_{i}")
                        c_a, c_b = st.columns(2)
                        qt = c1.number_input("數量", 1.0, step=0.5, key=f"q_{i}")
                        pr = 0
                        if conf["type"] == 'cost':
                            pr = c_b.number_input("單價", 0, step=100, key=f"p_{i}")
                        un = "式"
                    
                    im = st.file_uploader("照", type=['jpg','png'], key=f"m_{i}")
                    if st.form_submit_button("💾 儲存"):
                        process_append(conf["key"], conf["type"], it, un, qt, pr, nt, im)

# === Tab 2: 報表總覽 ===
with tab_data:
    if df.empty:
        st.info("尚無資料")
    else:
        c1, c2, c3 = st.columns([2, 2, 2])
        months = sorted(df['月份'].unique().tolist(), reverse=True)
        with c1: ed_month = st.selectbox("編輯月份", months)
        month_df = df[(df['月份'] == ed_month) & (df['專案'] == global_project)].copy()
        dates = sorted(month_df['日期'].unique().tolist())
        with c2: ed_date = st.selectbox("日期篩選", ["整個月"] + [str(d) for d in dates])
        with c3: search = st.text_input("搜尋關鍵字")
        st.divider()

        def render_online_section(cat_key, cat_disp, cat_type, key):
            sk = f"conf_{key}"
            if sk not in st.session_state: st.session_state[sk] = False
            
            sec_df = month_df[month_df['類別'] == cat_key].copy()
            if not sec_df.empty:
                st.subheader(cat_disp)
                view = sec_df.copy()
                if ed_date != "整個月": view = view[view['日期'].astype(str) == str(ed_date)]
                if search: mask = view.apply(lambda x: search in str(x['名稱']) or search in str(x['備註']), axis=1); view = view[mask]
                
                if not view.empty:
                    # 顯示處理
                    view['備註_顯示'] = view['備註'].apply(lambda x: f"✅ {remove_image_tag(x)}" if extract_image_from_note(x) else remove_image_tag(x))
                    
                    # 隱藏原始備註，只顯示處理過的
                    # 但 Streamlit data_editor 不支援 hidden=True，所以我們 drop 掉，用 index 找回
                    display_df = view.drop(columns=['備註']) 
                    
                    # 欄位設定
                    col_cfg = {
                        "備註_顯示": st.column_config.TextColumn(label="備註 (✅=有圖)", width="large"),
                        "日期": st.column_config.TextColumn(width="small"),
                        "總價": st.column_config.NumberColumn(disabled=True)
                    }
                    
                    edited = st.data_editor(
                        display_df,
                        key=f"e_{key}",
                        column_config=col_cfg,
                        use_container_width=True,
                        num_rows="dynamic" # 允許刪除
                    )
                    
                    # 線上版編輯邏輯較複雜，這裡提供「刪除」與「看圖」
                    # 檢查刪除
                    if len(edited) < len(display_df):
                        # 有資料被刪除了
                        # 找出被刪除的 index
                        deleted_indices = set(display_df.index) - set(edited.index)
                        if deleted_indices:
                            if st.button(f"確認刪除 {len(deleted_indices)} 筆資料?", key=f"del_btn_{key}"):
                                # 更新 global df
                                df_new = df.drop(index=list(deleted_indices))
                                update_sheet_data_online(df_new)
                                st.success("已刪除")
                                time.sleep(1); st.rerun()

                    # 圖片檢視 (簡單版：列出有圖的連結)
                    st.caption("📸 照片連結：")
                    has_img = False
                    for idx, row in view.iterrows():
                        img_link = extract_image_from_note(row['備註'])
                        if img_link:
                            has_img = True
                            st.markdown(f"- {row['日期']} {row['名稱']}: [開啟照片]({img_link})")
                    if not has_img: st.caption("無照片")

        for config in CAT_CONFIG_LIST:
            render_online_section(config["key"], config["display"], config["type"], f"sec_{config['key']}")

# === Tab 3: 成本儀表板 ===
with tab_dash:
    if df.empty: st.info("無資料")
    else:
        dash_df = df[df['專案'] == global_project]
        if dash_df.empty: st.warning("無專案資料")
        else:
            total = dash_df['總價'].sum()
            st.metric("專案總費用", f"${total:,.0f}")
            cost_df = dash_df[dash_df['總價'] > 0]
            if not cost_df.empty:
                bar = cost_df.groupby('類別')['總價'].sum().reset_index()
                st.bar_chart(bar, x='類別', y='總價')

# === Tab 4: 設定 (恢復您要的完整介面) ===
with tab_settings:
    st.header("⚙️ 設定與管理")
    
    with st.expander("1. 專案管理", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            new_p = st.text_input("新增專案名稱")
            if st.button("➕ 新增專案"):
                if new_p and new_p not in settings_data["projects"]: 
                    settings_data["projects"].append(new_p)
                    settings_data["items"][new_p] = {}
                    for config in CAT_CONFIG_LIST:
                        settings_data["items"][new_p][config["key"]] = DEFAULT_ITEMS.get(config["key"], [])
                    save_settings_online(settings_data); st.rerun()
        with c2:
            ren_p = st.text_input("修改目前專案名稱", value=global_project)
            if st.button("✏️ 確認改名"):
                if ren_p != global_project:
                    idx = settings_data["projects"].index(global_project)
                    settings_data["projects"][idx] = ren_p
                    settings_data["items"][ren_p] = settings_data["items"].pop(global_project)
                    if global_project in price_data:
                        price_data[ren_p] = price_data.pop(global_project)
                        settings_data["prices"] = price_data
                    save_settings_online(settings_data)
                    with st.spinner("更新歷史資料中..."):
                        df.loc[df['專案'] == global_project, '專案'] = ren_p
                        update_sheet_data_online(df)
                    st.session_state.mem_project = ren_p
                    st.success("改名成功"); time.sleep(1); st.rerun()

    # 3. 標題與選單項目管理
    st.subheader("3. 標題與選單項目管理")
    st.caption(f"正在設定：**{global_project}**")
    
    with st.expander("🔧 管理日報大標題 (修改名稱 / 新增管理項目)", expanded=False):
        st.markdown("##### 修改現有標題名稱")
        for i, config in enumerate(CAT_CONFIG_LIST):
            c_old, c_new, c_act = st.columns([2, 2, 1])
            with c_old: st.text(f"原標題: {config['display']}")
            with c_new: new_disp = st.text_input(f"新名稱 {i}", value=config['display'], label_visibility="collapsed")
            with c_act: 
                if new_disp != config['display']:
                    if st.button("更新", key=f"upd_cat_{i}"):
                        update_category_config(i, new_disp, settings_data)
                        st.success("更新成功"); time.sleep(0.5); st.rerun()
        
        st.markdown("---")
        st.markdown("#### ➕ 新增管理項目")
        c_n, c_t, c_b = st.columns([2, 2, 1])
        with c_n: new_block_name = st.text_input("區塊名稱 (如: 07.安全檢查)")
        with c_t: new_block_type = st.selectbox("類型", ["text", "usage", "cost"], format_func=lambda x: {"text": "文字紀錄", "usage": "數量管理", "cost": "成本統計"}[x])
        with c_b: 
            st.write("")
            if st.button("新增"):
                new_key = new_block_name.split('.')[-1].strip() if '.' in new_block_name else new_block_name
                if add_new_category_block(new_key, new_block_name, new_block_type, settings_data):
                    st.success("已新增"); time.sleep(0.5); st.rerun()
                else: st.error("區塊 Key 已存在")

    st.divider()

    # B. 選單項目管理 (恢復單價/單位設定)
    cat_options = [c["display"] for c in CAT_CONFIG_LIST]
    target_display = st.selectbox("選擇要管理項目的類別", cat_options)
    target_config = next((c for c in CAT_CONFIG_LIST if c["display"] == target_display), None)
    
    if target_config:
        target_key = target_config["key"]
        cat_type = target_config["type"]
        curr_list = settings_data["items"][global_project].get(target_key, [])
        
        c_add, c_act = st.columns([3, 1])
        with c_add: new_option = st.text_input(f"在【{target_display}】新增選單項目", key=f"new_opt_{target_key}")
        with c_act:
            st.write(""); st.write("")
            if st.button("➕ 加入項目", key=f"btn_add_{target_key}"):
                if new_option and new_option not in curr_list:
                    settings_data["items"][global_project][target_key].append(new_option)
                    save_settings_online(settings_data)
                    st.success(f"已加入"); time.sleep(0.5); st.rerun()

        st.markdown(f"##### 管理現有項目 ({len(curr_list)})")
        
        # 標題列
        if cat_type == 'cost':
            h1, h2, h3, h4, h5, h6 = st.columns([2, 2, 1, 1, 1, 1])
            h1.markdown("**原名稱**"); h2.markdown("**新名稱**"); h3.markdown("**單價**"); h4.markdown("**單位**"); h5.markdown("**存**"); h6.markdown("**刪**")
        else:
            h1, h2, h5, h6 = st.columns([3, 3, 1, 1])
            h1.markdown("**原名稱**"); h2.markdown("**新名稱**"); h5.markdown("**存**"); h6.markdown("**刪**")

        for item in curr_list:
            if cat_type == 'cost':
                c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 1, 1, 1, 1])
            else:
                c1, c2, c5, c6 = st.columns([3, 3, 1, 1])
            
            with c1: st.text(item)
            with c2: new_name_input = st.text_input("改名", value=item, key=f"ren_{target_key}_{item}", label_visibility="collapsed")
            
            new_p, new_u = 0, ""
            if cat_type == 'cost':
                if target_key not in price_data[global_project]: price_data[global_project][target_key] = {}
                p_info = price_data[global_project][target_key].get(item, {"price": 0, "unit": "工"})
                with c3: new_p = st.number_input("單價", value=float(p_info["price"]), key=f"p_{target_key}_{item}", label_visibility="collapsed")
                with c4: new_u = st.text_input("單位", value=p_info["unit"], key=f"u_{target_key}_{item}", label_visibility="collapsed")
            
            with c5:
                if st.button("💾", key=f"save_{target_key}_{item}"):
                    if new_name_input != item:
                        update_item_name(global_project, target_key, item, new_name_input, settings_data, price_data)
                    if cat_type == 'cost':
                        final_name = new_name_input if new_name_input != item else item
                        if target_key not in price_data[global_project]: price_data[global_project][target_key] = {}
                        price_data[global_project][target_key][final_name] = {"price": new_p, "unit": new_u}
                        settings_data["prices"] = price_data # 更新到大設定
                        save_settings_online(settings_data)
                    st.toast("更新成功"); time.sleep(0.5); st.rerun()

            with c6:
                if st.button("🗑️", key=f"del_{target_key}_{item}"):
                    settings_data["items"][global_project][target_key].remove(item)
                    save_settings_online(settings_data)
                    st.rerun()