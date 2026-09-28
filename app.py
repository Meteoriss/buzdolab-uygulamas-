import streamlit as st
import requests
import json
import os
import re
from datetime import datetime, date, timedelta
from io import BytesIO

# --- Firebase ---
import firebase_admin
from firebase_admin import credentials, firestore

# --- Çeviri ---
from deep_translator import GoogleTranslator

# --- Barkod ---
from pyzbar.pyzbar import decode
from PIL import Image

# --- Scraping ---
from bs4 import BeautifulSoup

# =========================================================
# FIREBASE INIT
# =========================================================
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()
FIREBASE_API_KEY = "BURAYA_WEB_API_KEY"

# =========================================================
# ÇEVİRİ
# =========================================================
def t(text, target="tr"):
    if not text:
        return text
    try:
        return GoogleTranslator(source="auto", target=target).translate(text[:4500])
    except Exception:
        return text

@st.cache_data(ttl=86400, show_spinner=False)
def t_cached(text, target="tr"):
    return t(text, target)

# =========================================================
# KİMLİK DOĞRULAMA
# =========================================================
def fb_sign_up(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_API_KEY}"
    return requests.post(url, json={"email": email, "password": password, "returnSecureToken": True}).json()

def fb_sign_in(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
    return requests.post(url, json={"email": email, "password": password, "returnSecureToken": True}).json()

def fb_send_reset(email):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={FIREBASE_API_KEY}"
    return requests.post(url, json={"requestType": "PASSWORD_RESET", "email": email}).json()

# =========================================================
# FIRESTORE
# =========================================================
def user_doc(uid):
    return db.collection("users").document(uid)

def load_inventory(uid):
    doc = user_doc(uid).get()
    return doc.to_dict().get("inventory", []) if doc.exists else []

def save_inventory(uid, inv):
    user_doc(uid).set({"inventory": inv}, merge=True)

def load_favorites(uid):
    doc = user_doc(uid).get()
    return doc.to_dict().get("favorites", []) if doc.exists else []

def save_favorites(uid, favs):
    user_doc(uid).set({"favorites": favs}, merge=True)

# =========================================================
# BARKOD OKUMA
# =========================================================
@st.cache_data(ttl=86400, show_spinner=False)
def lookup_barcode(barcode):
    """OpenFoodFacts API ile barkoddan ürün bilgisi çeker."""
    try:
        url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get("status") == 1:
            p = data["product"]
            return {
                "name": p.get("product_name") or p.get("generic_name") or "Bilinmeyen ürün",
                "brand": p.get("brands", ""),
                "quantity": p.get("quantity", ""),
                "barcode": barcode
            }
    except Exception:
        pass
    return None

def decode_barcode_from_image(image: Image.Image):
    """Yüklenen görselden barkod çözer."""
    try:
        results = decode(image)
        if results:
            return results[0].data.decode("utf-8")
    except Exception:
        pass
    return None

# =========================================================
# TARİF API — TheMealDB
# =========================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_recipes_by_ingredient(ingredient):
    try:
        url = f"https://www.themealdb.com/api/json/v1/1/filter.php?i={ingredient}"
        return (requests.get(url, timeout=10).json().get("meals") or [])
    except Exception:
        return []

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_recipe_detail(meal_id):
    try:
        url = f"https://www.themealdb.com/api/json/v1/1/lookup.php?i={meal_id}"
        return (requests.get(url, timeout=10).json().get("meals") or [None])[0]
    except Exception:
        return None

def get_recipe_ingredients(meal):
    items = []
    for i in range(1, 21):
        ing = meal.get(f"strIngredient{i}")
        meas = meal.get(f"strMeasure{i}")
        if ing and ing.strip():
            items.append((ing.strip().lower(), (meas or "").strip()))
    return items

# =========================================================
# TARİF KAYNAĞI — Nefis Yemek Tarifleri (Türkçe)
# =========================================================
@st.cache_data(ttl=3600, show_spinner=False)
def search_turkish_recipes(query):
    """Nefis Yemek Tarifleri'nden arama sonuçlarını çeker."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; FridgeApp/1.0)"}
        url = f"https://www.nefisyemektarifleri.com/ara/?q={requests.utils.quote(query)}"
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        results = []
        for card in soup.select("article, .recipe-card, .search-result-item")[:15]:
            a = card.find("a", href=True)
            title_el = card.find(["h2", "h3", "h4"])
            if a and title_el:
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": a["href"] if a["href"].startswith("http") else "https://www.nefisyemektarifleri.com" + a["href"],
                    "source": "Nefis Yemek Tarifleri"
                })
        return results
    except Exception:
        return []

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_turkish_recipe_detail(url):
    """Nefis Yemek Tarifleri tarif detay sayfasını çeker."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; FridgeApp/1.0)"}
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")

        title = soup.find("h1")
        title = title.get_text(strip=True) if title else "Tarif"

        # Malzemeler
        ingredients = []
        for li in soup.select(".recipe-ingredients li, .ingredients li, ul.ingredient-list li"):
            txt = li.get_text(strip=True)
            if txt:
                ingredients.append(txt)

        # Yapılış
        steps = []
        for p in soup.select(".recipe-instructions p, .instructions p, ol.preparation-steps li"):
            txt = p.get_text(strip=True)
            if txt:
                steps.append(txt)

        # Görsel
        img = soup.find("img", class_=re.compile("recipe|main|hero", re.I))
        image_url = img["src"] if img and img.get("src") else ""

        return {
            "title": title,
            "ingredients": ingredients,
            "instructions": "\n".join(steps),
            "image": image_url,
            "url": url,
            "source": "Nefis Yemek Tarifleri"
        }
    except Exception:
        return None

# =========================================================
# PROTEİN & GRAMAJ
# =========================================================
PROTEIN_KEYWORDS = [
    "chicken", "beef", "pork", "fish", "salmon", "tuna", "egg",
    "lamb", "turkey", "shrimp", "prawn", "cheese", "yogurt",
    "lentil", "bean", "chickpea", "tofu", "steak", "meat",
    "tavuk", "et", "balık", "yumurta", "mercimek", "nohut",
    "peynir", "yoğurt", "kıyma", "somon", "ton"
]

def is_protein_rich(meal):
    ings = [i[0] for i in get_recipe_ingredients(meal)]
    return any(any(k in ing for k in PROTEIN_KEYWORDS) for ing in ings)

def is_protein_rich_text(text):
    low = text.lower()
    return any(k in low for k in PROTEIN_KEYWORDS)

def parse_grams(measure_str):
    s = (measure_str or "").lower().replace(",", ".")
    # Türkçe birimler
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|kilo)", s)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"(\d+(?:\.\d+)?)\s*(g|gr|gram)", s)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(su bardağı|bardak)", s)
    if m:
        return float(m.group(1)) * 200
    m = re.search(r"(\d+(?:\.\d+)?)\s*(yemek kaşığı|yk)", s)
    if m:
        return float(m.group(1)) * 15
    m = re.search(r"(\d+(?:\.\d+)?)\s*(tatlı kaşığı|tk)", s)
    if m:
        return float(m.group(1)) * 5
    m = re.search(r"(\d+(?:\.\d+)?)\s*(adet|tane)", s)
    if m:
        return float(m.group(1)) * 100
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1))
    return None

def normalize_name(name):
    return name.strip().lower()

# =========================================================
# UI
# =========================================================
st.set_page_config(page_title="Akıllı Buzdolabı", page_icon="🧊", layout="wide")
st.title("🧊 Akıllı Buzdolabı & Protein Tarif Asistanı")

if "uid" not in st.session_state:
    st.session_state.uid = None
    st.session_state.email = None

# ---------------- GİRİŞ ----------------
def login_screen():
    st.subheader("Giriş / Kayıt")
    tab_login, tab_signup, tab_reset = st.tabs(["Giriş Yap", "Kayıt Ol", "Şifremi Unuttum"])

    with tab_login:
        e = st.text_input("E-posta", key="login_email")
        p = st.text_input("Şifre", type="password", key="login_pass")
        if st.button("Giriş Yap"):
            res = fb_sign_in(e, p)
            if "idToken" in res:
                st.session_state.uid = res["localId"]
                st.session_state.email = res["email"]
                st.rerun()
            else:
                st.error(res.get("error", {}).get("message", "Giriş başarısız."))

    with tab_signup:
        e2 = st.text_input("E-posta", key="signup_email")
        p2 = st.text_input("Şifre (min 6 karakter)", type="password", key="signup_pass")
        if st.button("Kayıt Ol"):
            res = fb_sign_up(e2, p2)
            if "idToken" in res:
                st.session_state.uid = res["localId"]
                st.session_state.email = res["email"]
                st.rerun()
            else:
                st.error(res.get("error", {}).get("message", "Kayıt başarısız."))

    with tab_reset:
        e3 = st.text_input("E-posta", key="reset_email")
        if st.button("Sıfırlama Linki Gönder"):
            res = fb_send_reset(e3)
            if "error" in res:
                st.error(res["error"]["message"])
            else:
                st.success("Sıfırlama e-postası gönderildi.")

if not st.session_state.uid:
    login_screen()
    st.stop()

uid = st.session_state.uid

# --- Üst bar ---
c1, c2 = st.columns([4, 1])
c1.write(f"👤 **{st.session_state.email}**")
if c2.button("Çıkış Yap"):
    st.session_state.uid = None
    st.session_state.email = None
    st.rerun()

if "inventory" not in st.session_state:
    st.session_state.inventory = load_inventory(uid)
if "favorites" not in st.session_state:
    st.session_state.favorites = load_favorites(uid)

# ---------------- SEKMELER ----------------
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🥕 Envanter", "📷 Barkod Ekle", "🍳 Tarif Öner",
    "📋 Tarif Detayı", "⭐ Favoriler"
])

# ---------------- TAB 1: ENVANTER ----------------
with tab1:
    st.subheader("Buzdolabına Malzeme Ekle")
    with st.form("add_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            name = st.text_input("Malzeme adı (örn: domates)")
        with c2:
            grams = st.number_input("Miktar (gram)", min_value=1.0, step=10.0, value=200.0)
        with c3:
            exp = st.date_input("Son tüketim tarihi", value=date.today() + timedelta(days=7))
        if st.form_submit_button("Ekle") and name.strip():
            st.session_state.inventory.append({
                "name": normalize_name(name),
                "grams": float(grams),
                "expiry": exp.isoformat()
            })
            save_inventory(uid, st.session_state.inventory)
            st.success(f"{name} eklendi.")
            st.rerun()

    st.divider()
    st.subheader("Mevcut Envanter")

    if not st.session_state.inventory:
        st.info("Henüz malzeme eklenmedi.")
    else:
        today = date.today()
        for idx, item in enumerate(st.session_state.inventory):
            exp_date = datetime.fromisoformat(item["expiry"]).date()
            days_left = (exp_date - today).days
            if days_left < 0:
                status = "🔴 SÜRESİ GEÇTİ"
            elif days_left <= 2:
                status = f"🟠 {days_left} gün - ÖNCE KULLAN"
            elif days_left <= 5:
                status = f"🟡 {days_left} gün"
            else:
                status = f"🟢 {days_left} gün"

            cols = st.columns([3, 2, 3, 1])
            cols[0].write(f"**{item['name']}**")
            cols[1].write(f"{item['grams']:.0f} g")
            cols[2].write(f"{item['expiry']} — {status}")
            if cols[3].button("🗑️", key=f"del_{idx}"):
                st.session_state.inventory.pop(idx)
                save_inventory(uid, st.session_state.inventory)
                st.rerun()

# ---------------- TAB 2: BARKOD ----------------
with tab2:
    st.subheader("📷 Barkod ile Ürün Ekle")
    st.write("Ürünün barkodunu kamerayla veya galeriden yükle. Alternatif olarak barkod numarasını elle gir.")

    method = st.radio("Yöntem", ["📸 Fotoğraf Yükle", "⌨️ Barkod Numarası Gir"], horizontal=True)

    barcode_result = None

    if method == "📸 Fotoğraf Yükle":
        uploaded = st.file_uploader("Barkod fotoğrafı", type=["png", "jpg", "jpeg"])
        if uploaded:
            img = Image.open(uploaded)
            st.image(img, caption="Yüklenen görsel", width=300)
            with st.spinner("Barkod çözülüyor..."):
                barcode_result = decode_barcode_from_image(img)
            if not barcode_result:
                st.warning("Barkod bulunamadı. Daha net bir fotoğraf dene veya elle gir.")
            else:
                st.success(f"Barkod: {barcode_result}")
    else:
        manual = st.text_input("Barkod numarası (örn: 8690504010013)")
        if manual.strip():
            barcode_result = manual.strip()

    if barcode_result:
        with st.spinner("Ürün bilgisi aranıyor..."):
            product = lookup_barcode(barcode_result)

        if product:
            st.markdown(f"### {product['name']}")
            if product.get("brand"):
                st.write(f"**Marka:** {product['brand']}")
            if product.get("quantity"):
                st.write(f"**Miktar:** {product['quantity']}")

            with st.form("barcode_add_form"):
                c1, c2 = st.columns(2)
                with c1:
                    final_name = st.text_input("Malzeme adı", value=product["name"])
                with c2:
                    final_grams = st.number_input("Gram", min_value=1.0, value=100.0)
                exp_date = st.date_input("Son tüketim tarihi",
                                         value=date.today() + timedelta(days=7))
                if st.form_submit_button("Envantere Ekle"):
                    st.session_state.inventory.append({
                        "name": normalize_name(final_name),
                        "grams": float(final_grams),
                        "expiry": exp_date.isoformat()
                    })
                    save_inventory(uid, st.session_state.inventory)
                    st.success(f"{final_name} eklendi!")
                    st.rerun()
        else:
            st.warning("Ürün veritabanında bulunamadı. Elle ekleyebilirsin.")
            with st.form("manual_add"):
                c1, c2 = st.columns(2)
                with c1:
                    n = st.text_input("Malzeme adı")
                with c2:
                    g = st.number_input("Gram", min_value=1.0, value=100.0)
                e = st.date_input("Son tüketim tarihi", value=date.today() + timedelta(days=7))
                if st.form_submit_button("Ekle") and n.strip():
                    st.session_state.inventory.append({
                        "name": normalize_name(n),
                        "grams": float(g),
                        "expiry": e.isoformat()
                    })
                    save_inventory(uid, st.session_state.inventory)
                    st.rerun()

# ---------------- TAB 3: TARİF ÖNER ----------------
with tab3:
    st.subheader("Protein Ağırlıklı Tarif Önerileri")

    if not st.session_state.inventory:
        st.warning("Önce envanterine malzeme ekle.")
    else:
        today = date.today()
        priority_items = sorted(
            st.session_state.inventory,
            key=lambda x: datetime.fromisoformat(x["expiry"]).date()
        )
        priority_names = [i["name"] for i in priority_items
                          if (datetime.fromisoformat(i["expiry"]).date() - today).days <= 3]

        st.markdown("### ⚠️ Önce Kullanılması Gerekenler")
        if priority_names:
            st.write(", ".join(f"**{n}**" for n in priority_names))
        else:
            st.write("Acil tüketilmesi gereken malzeme yok.")

        st.divider()

        # Kaynak seçimi
        source = st.radio(
            "Tarif kaynağı",
            ["🌍 Uluslararası (TheMealDB + Türkçe çeviri)",
             "🇹🇷 Türkçe (Nefis Yemek Tarifleri)"],
            horizontal=True
        )

        st.markdown("### Öneriler")

        # --- ULUSLARARASI ---
        if source.startswith("🌍"):
            if st.button("🔄 Tarifleri Getir"):
                st.session_state["fetch_trigger"] = True

            if st.session_state.get("fetch_trigger"):
                search_terms = priority_names + [i["name"] for i in st.session_state.inventory]
                search_terms = list(dict.fromkeys(search_terms))
                found = {}
                prog = st.progress(0, text="Tarifler getiriliyor...")
                for idx, term in enumerate(search_terms[:10]):
                    for m in fetch_recipes_by_ingredient(term):
                        if m["idMeal"] in found:
                            continue
                        detail = fetch_recipe_detail(m["idMeal"])
                        if detail and is_protein_rich(detail):
                            found[m["idMeal"]] = detail
                    prog.progress((idx + 1) / min(len(search_terms), 10))
                prog.empty()
                st.session_state["found_recipes"] = list(found.values())

            recipes = st.session_state.get("found_recipes", [])
            if not recipes:
                st.info("'Tarifleri Getir' butonuna bas.")
            else:
                def score(meal):
                    ings = [i[0] for i in get_recipe_ingredients(meal)]
                    return sum(10 for p in priority_names if any(p in ing for ing in ings))

                for meal in sorted(recipes, key=score, reverse=True):
                    ings = get_recipe_ingredients(meal)
                    used_pri = [p for p in priority_names if any(p in i[0] for i in ings)]
                    tag = " ⚠️ Öncelikli!" if used_pri else ""
                    tr_title = t_cached(meal["strMeal"])

                    is_fav = any(f["idMeal"] == meal["idMeal"] for f in st.session_state.favorites)
                    fav_icon = "⭐" if is_fav else "☆"

                    with st.expander(f"🍽️ {tr_title}{tag}"):
                        cc1, cc2 = st.columns([1, 2])
                        with cc1:
                            if meal.get("strMealThumb"):
                                st.image(meal["strMealThumb"], use_column_width=True)
                        with cc2:
                            st.write("**Malzemeler:**")
                            for ing, meas in ings:
                                st.write(f"- {t_cached(ing)}: {meas}")

                            if st.button(f"{fav_icon} Favori", key=f"fav_{meal['idMeal']}"):
                                favs = st.session_state.favorites
                                if is_fav:
                                    favs = [f for f in favs if f["idMeal"] != meal["idMeal"]]
                                else:
                                    favs.append({
                                        "idMeal": meal["idMeal"],
                                        "strMeal": meal["strMeal"],
                                        "strMealThumb": meal.get("strMealThumb", ""),
                                        "source": "themealdb"
                                    })
                                st.session_state.favorites = favs
                                save_favorites(uid, favs)
                                st.rerun()

                            if st.button("👨‍🍳 Detay", key=f"cook_{meal['idMeal']}"):
                                st.session_state["cook_meal"] = meal["idMeal"]
                                st.session_state["cook_source"] = "themealdb"
                                st.success("Tarif seçildi! 'Tarif Detayı' sekmesine git.")
                                st.rerun()

        # --- TÜRKÇE ---
        else:
            query_input = st.text_input("Aramak istediğin malzeme (örn: tavuk, yumurta)",
                                        value=priority_names[0] if priority_names else "")
            if st.button("🇹🇷 Türkçe Tarif Ara"):
                if query_input.strip():
                    with st.spinner("Nefis Yemek Tarifleri'nde aranıyor..."):
                        st.session_state["tr_results"] = search_turkish_recipes(query_input.strip())

            tr_results = st.session_state.get("tr_results", [])
            if not tr_results:
                st.info("Bir malzeme yaz ve ara.")
            else:
                for idx, r in enumerate(tr_results):
                    is_fav = any(f.get("url") == r["url"] for f in st.session_state.favorites)
                    fav_icon = "⭐" if is_fav else "☆"
                    with st.expander(f"🍽️ {r['title']}"):
                        st.write(f"Kaynak: {r['source']}")
                        st.write(r["url"])

                        if st.button(f"{fav_icon} Favori", key=f"tr_fav_{idx}"):
                            favs = st.session_state.favorites
                            if is_fav:
                                favs = [f for f in favs if f.get("url") != r["url"]]
                            else:
                                favs.append({
                                    "url": r["url"],
                                    "strMeal": r["title"],
                                    "strMealThumb": "",
                                    "source": "nefis"
                                })
                            st.session_state.favorites = favs
                            save_favorites(uid, favs)
                            st.rerun()

                        if st.button("👨‍🍳 Detay", key=f"tr_cook_{idx}"):
                            with st.spinner("Tarif yükleniyor..."):
                                detail = fetch_turkish_recipe_detail(r["url"])
                            if detail:
                                st.session_state["cook_tr_detail"] = detail
                                st.session_state["cook_source"] = "nefis"
                                st.success("Tarif yüklendi! 'Tarif Detayı' sekmesine git.")
                                st.rerun()
                            else:
                                st.error("Tarif detayı alınamadı.")

# ---------------- TAB 4: TARİF DETAYI ----------------
with tab4:
    st.subheader("Tarifi Uygula ve Envanterden Düş")
    source = st.session_state.get("cook_source")

    if source == "themealdb" and st.session_state.get("cook_meal"):
        meal = fetch_recipe_detail(st.session_state["cook_meal"])
        st.write(f"### {t_cached(meal['strMeal'])}")
        with st.spinner("Çevriliyor..."):
            st.write(t_cached(meal.get("strInstructions", "")))

        ings = get_recipe_ingredients(meal)
        st.write("**Malzemeler ve stoktan düşülecek miktarlar:**")
        deductions = []
        for ing, meas in ings:
            grams_needed = parse_grams(meas)
            inv_item = next((i for i in st.session_state.inventory
                             if i["name"] in ing or ing in i["name"]), None)
            if inv_item and grams_needed:
                st.write(f"- **{inv_item['name']}**: {grams_needed} g (stokta {inv_item['grams']:.0f} g)")
                deductions.append((inv_item["name"], grams_needed))
            elif inv_item:
                st.write(f"- **{inv_item['name']}**: miktar belirsiz")
            else:
                st.write(f"- {t_cached(ing)}: envanterde yok")

        if st.button("✅ Onayla ve Düş"):
            for name_, grams in deductions:
                for item in list(st.session_state.inventory):
                    if item["name"] == name_:
                        item["grams"] -= grams
                        if item["grams"] <= 0:
                            st.session_state.inventory.remove(item)
                        break
            save_inventory(uid, st.session_state.inventory)
            st.session_state.pop("cook_meal", None)
            st.session_state.pop("cook_source", None)
            st.success("Envanter güncellendi!")
            st.rerun()

    elif source == "nefis" and st.session_state.get("cook_tr_detail"):
        d = st.session_state["cook_tr_detail"]
        st.write(f"### {d['title']}")
        if d.get("image"):
            st.image(d["image"], width=400)
        st.write(f"Kaynak: [{d['source']}]({d['url']})")

        st.write("**Malzemeler:**")
        for ing in d["ingredients"]:
            st.write(f"- {ing}")

        st.write("**Yapılışı:**")
        st.write(d["instructions"])

        # Envanterden düşme (Türkçe malzemeleri eşleştir)
        st.divider()
        st.write("### Envanterden Düşülecekler")
        deductions = []
        for ing in d["ingredients"]:
            grams_needed = parse_grams(ing)
            inv_item = None
            for item in st.session_state.inventory:
                if item["name"] in ing.lower():
                    inv_item = item
                    break
            if inv_item and grams_needed:
                st.write(f"- **{inv_item['name']}**: {grams_needed} g (stokta {inv_item['grams']:.0f} g)")
                deductions.append((inv_item["name"], grams_needed))
            elif inv_item:
                st.write(f"- **{inv_item['name']}**: miktar belirsiz, manuel düş")
            else:
                st.write(f"- {ing}: envanterde yok")

        if st.button("✅ Onayla ve Düş", key="nefis_deduct"):
            for name_, grams in deductions:
                for item in list(st.session_state.inventory):
                    if item["name"] == name_:
                        item["grams"] -= grams
                        if item["grams"] <= 0:
                            st.session_state.inventory.remove(item)
                        break
            save_inventory(uid, st.session_state.inventory)
            st.session_state.pop("cook_tr_detail", None)
            st.session_state.pop("cook_source", None)
            st.success("Envanter güncellendi!")
            st.rerun()

    else:
        st.info("Önce 'Tarif Öner' sekmesinden bir tarif seç.")

# ---------------- TAB 5: FAVORİLER ----------------
with tab5:
    st.subheader("⭐ Favori Tariflerim")
    if not st.session_state.favorites:
        st.info("Henüz favori yok.")
    else:
        for idx, fav in enumerate(st.session_state.favorites):
            src = fav.get("source", "themealdb")
            title = fav["strMeal"] if src == "nefis" else t_cached(fav["strMeal"])
            with st.expander(f"🍽️ {title}  ({src})"):
                c1, c2 = st.columns([1, 3])
                with c1:
                    if fav.get("strMealThumb"):
                        st.image(fav["strMealThumb"], use_column_width=True)
                with c2:
                    if src == "themealdb":
                        if st.button("👨‍🍳 Detay", key=f"fav_cook_{idx}"):
                            st.session_state["cook_meal"] = fav["idMeal"]
                            st.session_state["cook_source"] = "themealdb"
                            st.success("Seçildi! 'Tarif Detayı' sekmesine git.")
                    else:
                        st.write(fav.get("url", ""))
                        if st.button("👨‍🍳 Detay", key=f"fav_tr_cook_{idx}"):
                            with st.spinner("Yükleniyor..."):
                                detail = fetch_turkish_recipe_detail(fav["url"])
                            if detail:
                                st.session_state["cook_tr_detail"] = detail
                                st.session_state["cook_source"] = "nefis"
                                st.success("Seçildi! 'Tarif Detayı' sekmesine git.")
                    if st.button("🗑️ Çıkar", key=f"fav_del_{idx}"):
                        st.session_state.favorites.pop(idx)
                        save_favorites(uid, st.session_state.favorites)
                        st.rerun()
                        