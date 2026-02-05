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
import re # [Fix] Added missing import
from PIL import Image # [Fix] Added missing import

# ==========================================
# 0. System Configuration
# ==========================================
st.set_page_config(page_title="Multi-Project Construction Management System (Secure Login)", layout="wide", page_icon="🔒")

# --- 🔐 Security Settings (Change password here) ---
SYSTEM_PASSWORD = "12345"  # <--- Please change your login password here

# --- File Paths ---
DATA_FILE = 'construction_data.csv' 
SETTINGS_FILE = 'settings.json'
TYPES_FILE = 'category_types.json'
PRICES_FILE = 'item_prices.json'
KEY_FILE = 'service_key.json'
SHEET_NAME = 'construction_db'
PHOTO_DIR = 'uploaded_photos'

# Ensure photo directory exists
if not os.path.exists(PHOTO_DIR):
    os.makedirs(PHOTO_DIR)

# --- Taiwan Holidays ---
HOLIDAYS = {
    "2025-01-01": "New Year's Day", "2025-01-27": "Little New Year's Eve", "2025-01-28": "New Year's Eve", "2025-01-29": "Spring Festival", "2025-01-30": "Second Day", "2025-01-31": "Third Day",
    "2025-02-28": "Peace Memorial Day", "2025-04-04": "Children's/Tomb Sweeping Day", "2025-05-01": "Labor Day", "2025-05-31": "Dragon Boat Festival",
    "2025-10-06": "Mid-Autumn Festival", "2025-10-10": "National Day",
    "2026-01-01": "New Year's Day", "2026-02-16": "Little New Year's Eve", "2026-02-17": "New Year's Eve", "2026-02-18": "Spring Festival",
    "2026-02-28": "Peace Memorial Day", "2026-04-04": "Children's Day", "2026-04-05": "Tomb Sweeping Day", "2026-05-01": "Labor Day",
    "2026-06-19": "Dragon Boat Festival", "2026-09-25": "Mid-Autumn Festival", "2026-10-10": "National Day"
}

# --- Default Data Structure ---
DEFAULT_TEMPLATE = {
    "Construction Description": ["Normal Construction", "Construction Suspended", "Finishing Stage", "Defect Rectification", "Bad Weather"],
    "Related Records": ["Today's Meeting", "Supervisor Walkthrough", "Major Event Record", "Safety Matters", "Site Inspection Record"],
    "Material Intake": ["Rebar Arrival", "Cement Intake", "Tile Intake", "Equipment Intake", "Other Materials"],
    "Material Usage": ["Concrete 3000psi", "Concrete 2500psi", "CLSM", "Graded Aggregate", "Cement Mortar"],
    "Labor (Manpower)": ["General Labor", "Masonry", "Plumbing/Electrical", "Painting", "Carpentry", "Ironwork", "Formwork", "Rebar Tying", "Demolition", "Cleaning"],
    "Machinery (Equipment)": ["Excavator", "Bobcat", "Crane", "Generator", "Air Compressor", "Breaker", "Compactor", "Truck"]
}

ORDER_MAP = {
    "Construction Description": "01. Construction Description", "Related Records": "02. Related Records", "Material Intake": "03. Material Intake",
    "Material Usage": "04. Material Usage", "Labor (Manpower)": "05. Labor (Manpower)", "Machinery (Equipment)": "06. Machinery (Equipment)"
}

DEFAULT_TYPES = {
    "Construction Description": "text", "Related Records": "text", "Material Intake": "text",
    "Material Usage": "usage", "Labor (Manpower)": "cost", "Machinery (Equipment)": "cost"
}

COST_CATEGORIES = [k for k, v in DEFAULT_TYPES.items() if v == 'cost']

# ==========================================
# 1. 🔐 Login Verification Logic (Gatekeeper)
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

def check_login():
    if st.session_state.password_input == SYSTEM_PASSWORD:
        st.session_state.logged_in = True
    else:
        st.error("❌ Incorrect password, please try again.")

if not st.session_state.logged_in:
    st.markdown("## 🔒 System Locked")
    st.markdown("To protect project data, please enter the password to continue.")
    st.text_input("Please enter password:", type="password", key="password_input", on_change=check_login)
    st.stop()

# ==========================================
# 2. Core Logic (Data I/O Layer)
# ==========================================

@st.cache_resource
def get_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = None
    if os.path.exists(KEY_FILE):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(KEY_FILE, scope)
        except Exception as e:
            st.error(f"Local key error: {e}")
            return None
    else:
        try:
            if "gcp_service_account" in st.secrets:
                creds_dict = st.secrets["gcp_service_account"]
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        except Exception:
            return None
            
    if creds is None:
        return None
        
    try:
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).sheet1
        return sheet
    except gspread.SpreadsheetNotFound:
        return "NOT_FOUND"
    except Exception as e:
        st.error(f"Connection error: {e}")
        return None

def get_date_info(date_obj):
    weekdays = ["(Mon)", "(Tue)", "(Wed)", "(Thu)", "(Fri)", "(Sat)", "(Sun)"]
    date_str = date_obj.strftime("%Y-%m-%d")
    w_str = weekdays[date_obj.weekday()]
    is_weekend = date_obj.weekday() >= 5
    if date_str in HOLIDAYS: return f"🔴 {w_str} ★{HOLIDAYS[date_str]}", True 
    if is_weekend: return f"🔴 {w_str}", True 
    return f"{w_str}", False

# [FIX] Corrected SyntaxError: Separated try and with blocks
def load_json(filepath, default_data):
    if not os.path.exists(filepath):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(default_data, f, ensure_ascii=False, indent=4)
        return default_data
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return default_data

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_settings():
    data = load_json(SETTINGS_FILE, {"projects": ["Default Project"], "items": {"Default Project": copy.deepcopy(DEFAULT_TEMPLATE)}})
    save_json(SETTINGS_FILE, data)
    return data

def save_settings(data):
    save_json(SETTINGS_FILE, data)

def load_prices(): 
    data = load_json(PRICES_FILE, {})
    save_json(PRICES_FILE, data)
    return data

def save_prices(data):
    save_json(PRICES_FILE, data)
    
def save_types(data):
    save_json(TYPES_FILE, data)

def load_data():
    cols = ['Date', 'Project', 'Category', 'Name', 'Unit', 'Quantity', 'Unit Price', 'Total Price', 'Remarks', 'Month']
    sheet = get_google_sheet()
    
    if sheet == "NOT_FOUND":
        st.error(f"Cloud spreadsheet not found: {SHEET_NAME}.")
        return pd.DataFrame(columns=cols)
    elif sheet is None:
        st.warning("⚠️ No key detected.")
        return pd.DataFrame(columns=cols)
        
    try:
        data = sheet.get_all_records()
        if not data:
            return pd.DataFrame(columns=cols)
            
        df = pd.DataFrame(data)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
                
        for col in ['Project', 'Category', 'Name', 'Unit', 'Remarks']:
            df[col] = df[col].fillna("").astype(str)
            
        df['Category'] = df['Category'].replace({
            'Today\'s Construction Overview': 'Construction Description', '01.Today\'s Construction Overview': 'Construction Description', 
            'Site Text Record': 'Related Records', 'Related Records (Meetings, Inspections, Walkthroughs, etc.)': 'Related Records'
        })
        
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df = df.dropna(subset=['Date'])
        df['Date'] = df['Date'].dt.date
        df['Month'] = pd.to_datetime(df['Date']).dt.strftime("%Y-%m")
        
        for col in ['Total Price', 'Quantity', 'Unit Price']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        return df
    except Exception as e:
        st.error(f"Read error: {e}")
        return pd.DataFrame(columns=cols)

def save_dataframe(df):
    try:
        sheet = get_google_sheet()
        if not sheet or sheet == "NOT_FOUND":
            return
        cols_drop = [c for c in ['Month', 'Delete', 'temp_month', 'Weekday/Holiday'] if c in df.columns]
        df_save = df.drop(columns=cols_drop)
        df_save['Date'] = df_save['Date'].astype(str)
        sheet.clear()
        sheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())
    except Exception as e:
        st.error(f"Save error: {e}")

# Save photo to local function
def save_image_local(uploaded_file, project, category):
    if uploaded_file is not None:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_ext = os.path.splitext(uploaded_file.name)[1]
            safe_proj = "".join([c for c in project if c.isalnum() or c in (' ', '_')]).strip()
            filename = f"{timestamp}_{safe_proj}_{category}{file_ext}"
            file_path = os.path.join(PHOTO_DIR, filename)
            
            with open(file_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            return filename
        except Exception as e:
            st.error(f"Photo save failed: {e}")
            return None
    return None

# Tool: Extract filename from remarks
def extract_image_from_note(note_str):
    if not note_str: return None
    match = re.search(r'\(Img:(.*?)\)', str(note_str))
    if match: return match.group(1).strip()
    return None

# Tool: Remove image tag from remarks (for displaying plain text)
def remove_image_tag(note_str):
    if not note_str: return ""
    return re.sub(r'\(Img:.*?\)', '', str(note_str)).strip()

def append_data(date, project, category, name, unit, qty, price, note):
    total = qty * price if category in COST_CATEGORIES else 0
    row = [str(date), project, category, name, unit, qty, price, total, note]
    try:
        sheet = get_google_sheet()
        if sheet and sheet != "NOT_FOUND":
            sheet.append_row(row)
        else:
            st.error("Write error")
    except Exception as e:
        st.error(f"Write exception: {e}")

def update_by_scope(original_df, edited_part, proj, month, cats):
    original_df['temp_month'] = pd.to_datetime(original_df['Date']).dt.strftime("%Y-%m")
    mask = (original_df['temp_month'] == month) & (original_df['Project'] == proj) & (original_df['Category'].isin(cats))
    df_kept = original_df[~mask].copy()
    
    edited_clean = edited_part.drop(columns=[c for c in ['Delete', 'Weekday/Holiday'] if c in edited_part.columns])
    for col in ['Quantity', 'Unit Price']:
        edited_clean[col] = pd.to_numeric(edited_clean[col], errors='coerce').fillna(0)
    edited_clean['Total Price'] = edited_clean.apply(lambda r: r['Quantity']*r['Unit Price'] if r['Category'] in COST_CATEGORIES else 0, axis=1)
    
    return pd.concat([df_kept, edited_clean], ignore_index=True)

def rename_project_logic(old_name, new_name, settings, prices):
    if new_name in settings["projects"]:
        return False, "Duplicate name"
    idx = settings["projects"].index(old_name)
    settings["projects"][idx] = new_name
    settings["items"][new_name] = settings["items"].pop(old_name)
    
    if old_name in prices:
        prices[new_name] = prices.pop(old_name)
    save_prices(prices)
    save_settings(settings)
    
    df = load_data()
    if not df.empty:
        df.loc[df['Project'] == old_name, 'Project'] = new_name
        save_dataframe(df)
    return True, "Success"

def rename_item_in_project(project, category, old_item, new_item, settings, prices):
    curr = settings["items"][project][category]
    if new_item in curr:
        return False
    curr[curr.index(old_item)] = new_item
    
    if project in prices and category in prices[project] and old_item in prices[project][category]:
        prices[project][category][new_item] = prices[project][category].pop(old_item)
        save_prices(prices)
        
    df = load_data()
    if not df.empty:
        df.loc[(df['Project']==project) & (df['Category']==category) & (df['Name']==old_item), 'Name'] = new_item
        save_dataframe(df)
    save_settings(settings)
    return True

def create_zip_backup():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        df = load_data()
        csv_buffer = io.StringIO()
        df.drop(columns=[c for c in ['Month', 'Delete', 'temp_month', 'Weekday/Holiday'] if c in df.columns]).to_csv(csv_buffer, index=False)
        zip_file.writestr(DATA_FILE, csv_buffer.getvalue())
        for file in [SETTINGS_FILE, PRICES_FILE, TYPES_FILE]:
            if os.path.exists(file):
                zip_file.write(file)
        if os.path.exists(PHOTO_DIR):
            for root, dirs, files in os.walk(PHOTO_DIR):
                for file in files:
                    zip_file.write(os.path.join(root, file))
    buffer.seek(0)
    return buffer

# ==========================================
# 3. Initialization and Cache
# ==========================================
settings_data = load_settings()
category_types = load_json(TYPES_FILE, DEFAULT_TYPES)
price_data = load_prices()
all_cats = set()
for p in settings_data["items"]:
    for c in settings_data["items"][p]:
        all_cats.add(c)
for c in all_cats: 
    if c not in category_types:
        category_types[c] = "text"
save_json(TYPES_FILE, category_types)

df = load_data()

if 'mem_project' not in st.session_state:
    st.session_state.mem_project = settings_data["projects"][0] if settings_data["projects"] else "Default Project"
if 'mem_date' not in st.session_state:
    st.session_state.mem_date = datetime.now()
if 'last_check_date' not in st.session_state:
    st.session_state.last_check_date = st.session_state.mem_date

# ==========================================
# 4. Main Screen (Only executes here after successful login)
# ==========================================
st.title("🏗️ Multi-Project Construction Management System (Perfect Sync Version)")

sheet_status = get_google_sheet()
if sheet_status is None:
    st.warning("⚠️ No key detected. Please confirm service_key.json (Computer) or Secrets (Mobile) is configured.")
else:
    with st.sidebar:
        st.header("📅 Date and Project")
        proj_list = settings_data["projects"]
        if st.session_state.mem_project not in proj_list:
            st.session_state.mem_project = proj_list[0]
        idx_proj = proj_list.index(st.session_state.mem_project)
        global_project = st.selectbox("🏗️ Current Project", proj_list, index=idx_proj, key="global_proj")
        global_date = st.date_input("📅 Work Date", st.session_state.mem_date, key="global_date")
        
        if global_date != st.session_state.last_check_date:
            st.session_state.last_check_date = global_date
            components.html("""<script>var tabs=window.parent.document.querySelectorAll('[data-testid="stTab"]');if(tabs.length>0){tabs[0].click();}</script>""", height=0, width=0)
            
        day_str, is_red_day = get_date_info(global_date)
        if is_red_day:
            st.markdown(f"<h3 style='color: #FF4B4B;'>{global_date} {day_str}</h3>", unsafe_allow_html=True)
        else:
            st.markdown(f"### {global_date} {day_str}")
            
        st.session_state.mem_project = global_project
        st.session_state.mem_date = global_date
        
        if global_project not in settings_data["items"]:
            settings_data["items"][global_project] = copy.deepcopy(DEFAULT_TEMPLATE)
            save_settings(settings_data)
        current_items = settings_data["items"][global_project]
        
        st.divider()
        if st.button("🔄 Force Refresh Data"):
            st.cache_resource.clear()
            st.rerun()
            
        if st.button("🔒 Logout"):
            st.session_state.logged_in = False
            st.rerun()

    tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 Quick Daily Report Entry", "🛠️ Report Overview and Edit", "📊 Cost Dashboard", "⚙️ Settings and Management"])

    with tab_entry:
        st.info(f"Filling out: **{global_project}** / **{global_date} {day_str}**")
        d_key = str(global_date)
        
        with st.expander("📝 01. Construction Description and Related Records", expanded=True):
            cols_g1 = st.columns(2)
            with cols_g1[0]: 
                real_cat = next((c for c in current_items if "Construction" in c), None)
                if real_cat:
                    st.markdown("**1. Construction Description**")
                    with st.form(key=f"form_status_{d_key}"):
                        txt_item = st.selectbox("Item", current_items[real_cat], key=f"sel_status_{d_key}")
                        txt_content = st.text_area("Content", height=100, key=f"area_status_{d_key}")
                        img_file = st.file_uploader("📸 Upload Photo (Optional)", type=['jpg', 'jpeg', 'png'], key=f"img_status_{d_key}")
                        if st.form_submit_button("💾 Save Description"):
                            append_data(global_date, global_project, real_cat, txt_item, "Set", 1, 0, txt_content) # Assuming no local image saving in cloud version
                            st.toast("Saved, syncing...")
                            time.sleep(1.5)
                            st.rerun()
            with cols_g1[1]:
                real_cat = next((c for c in current_items if "Record" in c or "Records" in c), None)
                if real_cat:
                    st.markdown("**2. Related Records**")
                    with st.form(key=f"form_records_{d_key}"):
                        txt_item = st.selectbox("Item", current_items[real_cat], key=f"sel_records_{d_key}")
                        txt_content = st.text_area("Content", height=100, key=f"area_records_{d_key}")
                        img_file = st.file_uploader("📸 Upload Photo (Optional)", type=['jpg', 'jpeg', 'png'], key=f"img_records_{d_key}")
                        if st.form_submit_button("💾 Save Record"):
                            append_data(global_date, global_project, real_cat, txt_item, "Set", 1, 0, txt_content) # Assuming no local image saving in cloud version
                            st.toast("Saved, syncing...")
                            time.sleep(1.5)
                            st.rerun()

        with st.expander("🚛 02. Material Intake Record", expanded=True):
            real_cat = next((c for c in current_items if "Intake" in c), None)
            if real_cat:
                cols_g2 = st.columns(3)
                for i in range(3):
                    with cols_g2[i]:
                        st.markdown(f"**Intake {i+1}**")
                        with st.form(key=f"form_in_{i}_{d_key}"):
                            in_item = st.selectbox("Material Name", current_items[real_cat], key=f"in_sel_{i}_{d_key}")
                            c_q, c_u = st.columns(2)
                            with c_q: in_qty = st.number_input("Quantity", min_value=0.0, step=1.0, key=f"in_q_{i}_{d_key}")
                            with c_u: in_unit = st.text_input("Unit", value="Set", key=f"in_u_{i}_{d_key}")
                            in_note = st.text_input("Remarks", key=f"in_n_{i}_{d_key}")
                            img_file = st.file_uploader("📸 Upload Photo", type=['jpg', 'jpeg', 'png'], key=f"img_in_{i}_{d_key}")
                            if st.form_submit_button("💾 Save Intake"):
                                append_data(global_date, global_project, real_cat, in_item, in_unit, in_qty, 0, in_note) # Assuming no local image saving in cloud version
                                st.toast("Saved, syncing...")
                                time.sleep(1.5)
                                st.rerun()

        with st.expander("🧱 03. Material Usage Record", expanded=True):
            real_cat = next((c for c in current_items if "Usage" in c), None)
            if real_cat:
                cols_g3 = st.columns(3)
                for i in range(3):
                    with cols_g3[i]:
                        st.markdown(f"**Usage {i+1}**")
                        with st.form(key=f"form_use_{i}_{d_key}"):
                            use_item = st.selectbox("Material Name", current_items[real_cat], key=f"use_sel_{i}_{d_key}")
                            c_q, c_u = st.columns(2)
                            with c_q: use_qty = st.number_input("Quantity", min_value=0.0, step=0.5, key=f"use_q_{i}_{d_key}")
                            with c_u: use_unit = st.text_input("Unit", value="m3", key=f"use_u_{i}_{d_key}")
                            use_note = st.text_input("Remarks", key=f"use_n_{i}_{d_key}")
                            if st.form_submit_button("💾 Save Usage"):
                                append_data(global_date, global_project, real_cat, use_item, use_unit, use_qty, 0, use_note)
                                st.toast("Saved, syncing...")
                                time.sleep(1.5)
                                st.rerun()

        with st.expander("👷 04. Labor and Machinery Work Record", expanded=True):
            cols_g4 = st.columns(2)
            with cols_g4[0]:
                cat = next((c for c in current_items if "Labor" in c), None)
                if cat:
                    st.markdown("### 01. Labor (Manpower)")
                    proj_prices = price_data.get(global_project, {}).get(cat, {})
                    cost_item = st.selectbox("Item", current_items[cat], key=f"sel_{cat}_{d_key}")
                    
                    # [Added Feature] Read default values
                    item_setting = proj_prices.get(cost_item, {"price": 0, "unit": "Day"})
                    
                    c_q, c_p = st.columns(2)
                    with c_q: 
                        cost_qty = st.number_input("Quantity", min_value=0.0, step=0.5, value=1.0, key=f"qty_{cat}_{d_key}_{cost_item}")
                    with c_p: 
                        # Use float() to ensure value is a correct number type, key includes cost_item to update on switch
                        cost_price = st.number_input("Unit Price ($)", value=float(item_setting["price"]), step=100.0, key=f"price_{cat}_{d_key}_{cost_item}")
                    
                    cost_unit = st.text_input("Unit", value=item_setting["unit"], key=f"unit_{cat}_{d_key}_{cost_item}")
                    cost_note = st.text_input("Remarks", key=f"note_{cat}_{d_key}_{cost_item}")
                    
                    if st.button(f"💾 Add Labor", type="primary", key=f"btn_{cat}"):
                        append_data(global_date, global_project, cat, cost_item, cost_unit, cost_qty, cost_price, cost_note)
                        st.toast("Saved, syncing...")
                        time.sleep(1.5)
                        st.rerun()
            with cols_g4[1]:
                cat = next((c for c in current_items if "Machinery" in c), None)
                if cat:
                    st.markdown("### 02. Machinery (Equipment)")
                    proj_prices = price_data.get(global_project, {}).get(cat, {})
                    cost_item = st.selectbox("Item", current_items[cat], key=f"sel_{cat}_{d_key}")
                    
                    # [Added Feature] Read default values
                    item_setting = proj_prices.get(cost_item, {"price": 0, "unit": "Set"})
                    
                    c_q, c_p = st.columns(2)
                    with c_q: 
                        cost_qty = st.number_input("Quantity", min_value=0.0, step=0.5, value=1.0, key=f"qty_{cat}_{d_key}_{cost_item}")
                    with c_p: 
                        cost_price = st.number_input("Unit Price ($)", value=float(item_setting["price"]), step=100.0, key=f"price_{cat}_{d_key}_{cost_item}")
                    
                    cost_unit = st.text_input("Unit", value=item_setting["unit"], key=f"unit_{cat}_{d_key}_{cost_item}")
                    cost_note = st.text_input("Remarks", key=f"note_{cat}_{d_key}_{cost_item}")
                    
                    if st.button(f"💾 Add Machinery", type="primary", key=f"btn_{cat}"):
                        append_data(global_date, global_project, cat, cost_item, cost_unit, cost_qty, cost_price, cost_note)
                        st.toast("Saved, syncing...")
                        time.sleep(1.5)
                        st.rerun()

    with tab_data:
        st.subheader("🛠️ Report Editing and Viewing")
        proj_df = df[df['Project'] == global_project].copy()
        if proj_df.empty:
            st.info(f"Project 【{global_project}】 has no data")
        else:
            c1, c2, c3 = st.columns([2, 2, 2])
            months = sorted(proj_df['Month'].unique().tolist(), reverse=True)
            with c1: ed_month = st.selectbox("Edit Month", months, key="ed_m")
            month_df = proj_df[proj_df['Month'] == ed_month].copy()
            dates = sorted(month_df['Date'].unique().tolist())
            with c2: ed_date = st.selectbox("Date Filter", ["Whole Month"] + dates, key="ed_d")
            with c3: search = st.text_input("Search Keywords", key="search_key")
            st.divider()
            
            def extract_image_from_note(note_str):
                if not note_str: return None
                match = re.search(r'\(Img:(.*?)\)', str(note_str))
                if match: return match.group(1).strip()
                return None

            def remove_image_tag(note_str):
                if not note_str: return ""
                return re.sub(r'\(Img:.*?\)', '', str(note_str)).strip()

            def render_section(display_title, cats, key, cost=False, qty=False):
                sk = f"conf_{key}"
                if sk not in st.session_state:
                    st.session_state[sk] = False
                sec_df = month_df[month_df['Category'].isin(cats)].copy()
                if not sec_df.empty:
                    st.subheader(display_title)
                    view = sec_df.copy()
                    if ed_date != "Whole Month":
                        view = view[view['Date'] == ed_date]
                    if search:
                        mask = view.apply(lambda x: search in str(x['Name']) or search in str(x['Remarks']), axis=1)
                        view = view[mask]
                    if not view.empty:
                        view['🗓️ Weekday/Holiday'] = view['Date'].apply(lambda x: get_date_info(x)[0])
                        cols = list(view.columns)
                        cols.insert(1, cols.pop(cols.index('🗓️ Weekday/Holiday')))
                        view = view[cols]
                        
                        hidden = sec_df[~sec_df.index.isin(view.index)]
                        if 'Delete' not in view.columns:
                            view.insert(0, "Delete", False)
                            
                        # Handle remark display (hide filename, add checkmark)
                        def format_note_for_display(note):
                            has_img = extract_image_from_note(note)
                            clean_text = remove_image_tag(note)
                            return f"✅ {clean_text}" if has_img else clean_text

                        view['Original Remarks'] = view['Remarks'] # Backup original remarks
                        view['Remarks'] = view['Remarks'].apply(format_note_for_display)
                        
                        if '📸 View Img' not in view.columns:
                            view.insert(1, "📸 View Img", False)
                            
                        col_cfg = {
                            "Delete": st.column_config.CheckboxColumn(width="small"),
                            "📸 View Img": st.column_config.CheckboxColumn(width="small", help="Check to manage or view photos"),
                            "Original Remarks": st.column_config.Column(hidden=True),
                            "Date": st.column_config.DateColumn(format="YYYY-MM-DD", width="small"),
                            "🗓️ Weekday/Holiday": st.column_config.TextColumn(disabled=True, width="medium"),
                            "Name": st.column_config.TextColumn(width="medium"),
                            "Remarks": st.column_config.TextColumn(width="large", label="Remarks (✅=Has Img)"),
                            "Month": None, "Category": None, "Project": None
                        }
                        if cost:
                            col_cfg.update({
                                "Unit Price": st.column_config.NumberColumn(width="small"),
                                "Total Price": st.column_config.NumberColumn(disabled=True, width="small")
                            })
                        else:
                            col_cfg.update({"Unit Price": None, "Total Price": None})
                            
                        if qty:
                            col_cfg.update({
                                "Quantity": st.column_config.NumberColumn(width="small"),
                                "Unit": st.column_config.TextColumn(width="small")
                            })
                        else:
                            col_cfg.update({"Quantity": None, "Unit": None})
                            
                        edited = st.data_editor(
                            view.sort_values('Date', ascending=False),
                            key=f"e_{key}",
                            column_config=col_cfg,
                            use_container_width=True,
                            hide_index=True
                        )
                        
                        # [Photo Management Panel]
                        if not edited.empty and edited["📸 View Img"].any():
                            st.markdown("---")
                            st.markdown("#### 📸 Photo Management Panel")
                            selected_rows = edited[edited["📸 View Img"]]
                            
                            for index, row in selected_rows.iterrows():
                                # Use original remarks to find image
                                original_note = row['Original Remarks']
                                img_filename = extract_image_from_note(original_note)
                                row_name = row['Name']
                                
                                col_show, col_manage = st.columns([1, 1])
                                
                                with col_show:
                                    if img_filename:
                                        img_path = os.path.join(PHOTO_DIR, img_filename)
                                        if os.path.exists(img_path):
                                            st.success(f"🖼️ Current Photo: {img_filename}")
                                            try:
                                                image = Image.open(img_path)
                                                st.image(image, width=400)
                                            except:
                                                st.error("Photo file corrupted")
                                        else:
                                            st.warning(f"⚠️ Photo file not found: {img_filename}")
                                    else:
                                        st.info("ℹ️ No photo for this item currently")

                                with col_manage:
                                    st.write(f"🔧 **Management Actions ({row_name})**")
                                    if img_filename:
                                        if st.button("🗑️ Delete This Photo", key=f"del_img_{index}"):
                                            if img_path and os.path.exists(img_path):
                                                try: os.remove(img_path)
                                                except: pass
                                            # Update database: remove tag
                                            clean_note = remove_image_tag(original_note)
                                            
                                            mask = (df['Project'] == global_project) & (df['Remarks'] == original_note)
                                            if mask.any():
                                                idx_to_update = df[mask].index[0] # Update only first match
                                                df.at[idx_to_update, 'Remarks'] = clean_note
                                                save_dataframe(df)
                                                st.success("Photo deleted!")
                                                time.sleep(0.5); st.rerun()
                                    
                                    new_img = st.file_uploader(f"{'📤 Upload New' if not img_filename else '🔄 Replace'} Photo", type=['jpg', 'jpeg', 'png'], key=f"new_img_{index}")
                                    if new_img and st.button("💾 Save Photo", key=f"save_img_{index}"):
                                        if img_filename:
                                            old_path = os.path.join(PHOTO_DIR, img_filename)
                                            if os.path.exists(old_path):
                                                try: os.remove(old_path)
                                                except: pass
                                        
                                        saved_filename = save_image_local(new_img, global_project, row['Category'])
                                        clean_note = remove_image_tag(original_note)
                                        new_note_str = f"{clean_note} (Img:{saved_filename})" if clean_note else f"(Img:{saved_filename})"
                                        
                                        mask = (df['Project'] == global_project) & (df['Remarks'] == original_note)
                                        if mask.any():
                                            idx_to_update = df[mask].index[0]
                                            df.at[idx_to_update, 'Remarks'] = new_note_str
                                            save_dataframe(df)
                                            st.success("Photo updated!")
                                            time.sleep(0.5); st.rerun()
                            st.markdown("---")
                        
                        b1, b2, _ = st.columns([1, 1, 6])
                        with b1: 
                            if st.button("💾 Update Changes", key=f"s_{key}"): 
                                # Restore remarks (combine display text with stored text)
                                for idx, row in edited.iterrows():
                                    user_text = str(row['Remarks']).replace("✅", "").strip()
                                    orig_note = str(row['Original Remarks'])
                                    img_tag = extract_image_from_note(orig_note)
                                    
                                    final_note = f"{user_text} (Img:{img_tag})" if img_tag else user_text
                                    edited.at[idx, 'Remarks'] = final_note
                                
                                vis = edited.drop(columns=['Delete', '📸 View Img', 'Original Remarks'])
                                merged = pd.concat([hidden, vis], ignore_index=True)
                                final = update_by_scope(df, merged, global_project, ed_month, cats)
                                save_dataframe(final)
                                st.toast("Update successful, syncing...")
                                time.sleep(1.5)
                                st.rerun()
                        with b2: 
                            if st.button("🗑️ Delete Selected", key=f"d_{key}", type="primary"): 
                                if not edited[edited['Delete']].empty:
                                    st.session_state[sk] = True
                                    
                        if st.session_state[sk]: 
                            st.warning("⚠️ Are you sure you want to delete selected items? This cannot be undone.")
                            cy, cn = st.columns([1, 5])
                            with cy:
                                if st.button("✔️ Yes", key=f"y_{key}", type="primary"): 
                                    # Restore remarks before deleting
                                    for idx, row in edited.iterrows():
                                        user_text = str(row['Remarks']).replace("✅", "").strip()
                                        orig_note = str(row['Original Remarks'])
                                        img_tag = extract_image_from_note(orig_note)
                                        final_note = f"{user_text} (Img:{img_tag})" if img_tag else user_text
                                        edited.at[idx, 'Remarks'] = final_note

                                    vis = edited[~edited['Delete']].drop(columns=['Delete', '📸 View Img', 'Original Remarks'])
                                    merged = pd.concat([hidden, vis], ignore_index=True)
                                    final = update_by_scope(df, merged, global_project, ed_month, cats)
                                    save_dataframe(final)
                                    st.session_state[sk] = False
                                    st.toast("Deletion successful, syncing...")
                                    time.sleep(1.5)
                                    st.rerun()
                            with cn:
                                if st.button("❌ No (Cancel)", key=f"n_{key}"):
                                    st.session_state[sk] = False
                                    st.rerun()
                                    
            for base_key, display_name in ORDER_MAP.items():
                target_cats = [c for c in current_items if base_key in c]
                if target_cats:
                    is_cost = "Labor" in base_key or "Machinery" in base_key
                    is_qty = "Intake" in base_key or "Usage" in base_key or is_cost
                    render_section(display_name, target_cats, f"sec_{base_key}", cost=is_cost, qty=is_qty)

    with tab_dash:
        if df.empty:
            st.info("No data")
        else:
            dash_df = df[df['Project'] == global_project]
            if dash_df.empty:
                st.warning(f"Project 【{global_project}】 currently has no data.")
            else:
                today_str = datetime.now().date()
                cur_month = today_str.strftime("%Y-%m")
                d_cost = dash_df[dash_df['Date'] == today_str]['Total Price'].sum()
                m_cost = dash_df[dash_df['Month'] == cur_month]['Total Price'].sum()
                t_cost = dash_df['Total Price'].sum()
                
                k1, k2, k3 = st.columns(3)
                k1.metric("Today's Cost", f"${d_cost:,.0f}")
                k2.metric("This Month's Cost", f"${m_cost:,.0f}")
                k3.metric("Project Total Cost", f"${t_cost:,.0f}")
                st.divider()
                
                cost_df = dash_df[dash_df['Total Price'] > 0]
                if not cost_df.empty:
                    months = sorted(cost_df['Month'].unique().tolist(), reverse=True)
                    c_sel, _ = st.columns([1,3])
                    with c_sel:
                        sel_chart_m = st.selectbox("Chart Statistics Month", months)
                    
                    chart_data = cost_df[cost_df['Month'] == sel_chart_m].copy()
                    if not chart_data.empty:
                        st.subheader(f"💰 {sel_chart_m} Cost Overview")
                        pie_data = chart_data.groupby('Category')['Total Price'].sum().reset_index()
                        
                        base = alt.Chart(pie_data).encode(theta=alt.Theta("Total Price", stack=True))
                        pie = base.mark_arc(outerRadius=100, innerRadius=50).encode(
                            color=alt.Color("Category"),
                            order=alt.Order("Total Price", sort="descending"),
                            tooltip=["Category", "Total Price"]
                        )
                        text = base.mark_text(radius=120).encode(
                            text=alt.Text("Total Price", format=",.0f"),
                            order=alt.Order("Total Price", sort="descending"),
                            color=alt.value("black")
                        )
                        st.altair_chart(pie + text, use_container_width=True)
                        st.divider()
                        
                        col_man, col_mach = st.columns(2)
                        with col_man:
                            st.markdown("### 👷 Labor Cost Details")
                            man_data = chart_data[chart_data['Category'].str.contains("Labor")]
                            if not man_data.empty:
                                man_bar = man_data.groupby('Name')['Total Price'].sum().reset_index()
                                st.bar_chart(man_bar, x='Name', y='Total Price', color="#FF6C6C")
                                st.dataframe(man_data[['Date', 'Name', 'Quantity', 'Unit Price', 'Total Price']], use_container_width=True, hide_index=True)
                                st.markdown(f"**Labor Total: ${man_data['Total Price'].sum():,.0f}**")
                            else:
                                st.info("No labor data")
                        with col_mach:
                            st.markdown("### 🚜 Machinery Cost Details")
                            mach_data = chart_data[chart_data['Category'].str.contains("Machinery")]
                            if not mach_data.empty:
                                mach_bar = mach_data.groupby('Name')['Total Price'].sum().reset_index()
                                st.bar_chart(mach_bar, x='Name', y='Total Price', color="#4B8BBE")
                                st.dataframe(mach_data[['Date', 'Name', 'Quantity', 'Unit Price', 'Total Price']], use_container_width=True, hide_index=True)
                                st.markdown(f"**Machinery Total: ${mach_data['Total Price'].sum():,.0f}**")
                            else:
                                st.info("No machinery data")
                    else:
                        st.info("No cost data for this month")
                else:
                    st.info("No cost records yet.")

    with tab_settings:
        st.header("⚙️ Settings and Management")
        
        with st.expander("📦 Data Backup Center", expanded=False):
            st.info("Download Backup (Includes cloud data and local settings)")
            st.download_button("📦 Download Full System Backup (ZIP)", create_zip_backup(), file_name=f"full_backup_{datetime.now().strftime('%Y%m%d')}.zip", mime="application/zip")
            st.divider()
            
            uploaded_file = st.file_uploader("📤 System Restore (Supports ZIP full package or CSV pure data)", type=['csv', 'zip'])
            if uploaded_file and st.button("⚠️ Confirm Restore"):
                try:
                    if uploaded_file.name.endswith('.csv'):
                        df_restore = pd.read_csv(uploaded_file)
                        save_dataframe(df_restore)
                        st.success("CSV Data Restore Successful! Syncing...")
                        time.sleep(1.5)
                        st.rerun()
                    elif uploaded_file.name.endswith('.zip'):
                        with zipfile.ZipFile(uploaded_file, 'r') as z:
                            z.extractall(".")
                            if DATA_FILE in z.namelist():
                                df_restore = pd.read_csv(DATA_FILE)
                                save_dataframe(df_restore)
                        st.success("Full System Restore Successful! Syncing...")
                        time.sleep(1.5)
                        st.rerun()
                except Exception as e:
                    st.error(f"Restore failed: {e}")
                    
        with st.expander("1. Project Management", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**Add Project**")
                with st.form("add_p"):
                    new_p = st.text_input("Project Name")
                    if st.form_submit_button("➕ Add"):
                        if new_p and new_p not in settings_data["projects"]:
                            settings_data["projects"].append(new_p)
                            settings_data["items"][new_p] = copy.deepcopy(DEFAULT_TEMPLATE)
                            save_settings(settings_data)
                            st.rerun()
            with c2:
                st.markdown("**Rename Project**")
                ren_p = st.text_input("Change to", value=global_project)
                if st.button("✏️ Confirm Rename"):
                    if ren_p != global_project:
                        suc, msg = rename_project_logic(global_project, ren_p, settings_data, price_data)
                        if suc:
                            st.session_state.mem_project = ren_p
                            st.success(msg)
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(msg)
            with c3:
                st.markdown("**Remove Project**")
                st.write("")
                confirm_del = st.checkbox("⚠️ Confirm removal of this project", key="confirm_del_proj")
                if st.button("🗑️ Confirm Removal", type="primary", disabled=not confirm_del):
                    if len(settings_data["projects"]) > 1:
                        settings_data["projects"].remove(global_project)
                        del settings_data["items"][global_project]
                        save_settings(settings_data)
                        st.session_state.mem_project = settings_data["projects"][0]
                        st.rerun()

        with st.expander("2. Import/Copy Project Settings (Template)", expanded=False):
            st.info("Copy settings from other projects to the current project.")
            src_opts = ["(System Default Template)"] + [p for p in settings_data["projects"] if p != global_project]
            src_p = st.selectbox("Select Source", src_opts)
            confirm_import = st.checkbox("⚠️ Confirm overwrite current settings", key="confirm_import_proj")
            
            if st.button("📥 Confirm Import", disabled=not confirm_import):
                src_items = DEFAULT_TEMPLATE if src_p == "(System Default Template)" else settings_data["items"][src_p]
                settings_data["items"][global_project] = copy.deepcopy(src_items)
                save_settings(settings_data)
                st.success("Import Successful")
                time.sleep(1)
                st.rerun()

        st.subheader("3. Independent Menu and Default Unit Price/Unit")
        st.caption(f"Configuring: **{global_project}**")
        
        if global_project in settings_data["items"]:
            p_items = settings_data["items"][global_project]
            if global_project not in price_data:
                price_data[global_project] = {}
            sorted_cats = []
            for base_key, display_name in ORDER_MAP.items():
                found = next((k for k in p_items.keys() if base_key in k), None)
                if found:
                    sorted_cats.append((found, display_name))
            
            col_s1, col_s2, col_s3 = st.columns(3)
            for i, (cat, display_name) in enumerate(sorted_cats):
                col = [col_s1, col_s2, col_s3][i % 3]
                with col:
                    st.info(f"📁 {display_name}")
                    curr_list = p_items[cat]
                    if cat not in price_data[global_project]:
                        price_data[global_project][cat] = {}
                        
                    with st.expander("Edit"):
                        with st.form(key=f"add_{cat}"):
                            ni = st.text_input("Add New")
                            if st.form_submit_button("Add"): 
                                if ni not in curr_list:
                                    settings_data["items"][global_project][cat].append(ni)
                                    save_settings(settings_data)
                                    st.rerun()
                                    
                        # [Added Feature] Display and edit unit price/unit
                        if cat in COST_CATEGORIES:
                            st.caption("💰 Default Unit Price and Unit")
                            for item_name in curr_list:
                                item_data = price_data[global_project][cat].get(item_name, {"price": 0, "unit": "Day" if "Labor" in cat else "Set"})
                                c_p, c_u, c_b = st.columns([2, 1, 1])
                                with c_p:
                                    # Fix: Ensure value is float type
                                    new_p = st.number_input(f"{item_name} Unit Price", value=float(item_data["price"]), step=100.0, key=f"p_{cat}_{item_name}", label_visibility="collapsed")
                                with c_u:
                                    new_u = st.text_input(f"Unit", value=item_data["unit"], key=f"u_{cat}_{item_name}", label_visibility="collapsed")
                                with c_b: 
                                    st.write("")
                                    st.write("")
                                    if st.button("✅", key=f"set_{cat}_{item_name}"):
                                        price_data[global_project][cat][item_name] = {"price": new_p, "unit": new_u}
                                        save_prices(price_data)
                                        st.toast(f"Saved: {item_name}")
                            st.write("---")
                            
                        target = st.selectbox("Select", curr_list, key=f"tgt_{cat}")
                        ren_txt = st.text_input("Rename", value=target, key=f"ren_{cat}")
                        c_e, c_d = st.columns(2)
                        with c_e: 
                            if st.button("Rename", key=f"btn_r_{cat}"): 
                                if ren_txt != target:
                                    rename_item_in_project(global_project, cat, target, ren_txt, settings_data, price_data)
                                    st.success("OK")
                                    st.rerun()
                        with c_d:
                            if st.button("Remove", key=f"btn_d_{cat}"):
                                settings_data["items"][global_project][cat].remove(target)
                                save_settings(settings_data)
                                st.rerun()