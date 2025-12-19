import time
import random
import re
import os  # ✅ ADDED: To get API Key
import requests
import resend  # ✅ ADDED: Resend Library
from bs4 import BeautifulSoup

# ⚠️ NOTE: We do NOT import 'app' at the top level to prevent crashing.
# We import it inside the functions instead.

# --- CONFIGURATION ---
MIN_WAIT = 5
MAX_WAIT = 10
HOURS_UNTIL_RESTOCK = 24

# ✅ CONFIGURE RESEND
resend.api_key = os.environ.get("RESEND_API_KEY")

# --- EMAIL FUNCTIONS (UPDATED FOR RESEND) ---
def send_price_drop_email(user_email, product_name, new_price, old_price, link, image, stock):
    try:
        print(f"⏳ Sending Price Drop Alert to {user_email}...", flush=True)
        
        subject = f"📉 Price Drop Alert: {product_name[:30]}..."
        stock_msg = f"<p style='color: red; font-weight: bold;'>⚠️ {stock}</p>" if stock else ""
        
        # ✅ NEW: Use Resend API
        r = resend.Emails.send({
            "from": "Naija Price Tracker <info@naija-price-tracker.name.ng>",
            "to": user_email,
            "subject": subject,
            "html": f"""
            <div style='font-family: Arial, sans-serif; max-width: 600px; margin: auto; border: 1px solid #eee; padding: 20px;'>
                <h2 style='color: #28a745;'>🔥 Price Drop Alert!</h2>
                <img src="{image}" style="width: 150px; border-radius: 5px;">
                <h3>{product_name}</h3>
                <p style='font-size: 16px;'>Old Price: <strike style='color: #888;'>₦{old_price:,.0f}</strike></p>
                <p style='font-size: 20px; font-weight: bold; color: #28a745;'>New Price: ₦{new_price:,.0f}</p>
                {stock_msg}
                <br>
                <a href="{link}" style="background-color: #FF9900; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold;">Buy Now on Jumia</a>
            </div>
            """
        })
        print(f"   ✅ Email sent! ID: {r.get('id')}")
    except Exception as e:
        print(f"   ❌ Email Failed: {e}")

def send_product_removed_email(user_email, product_name, link):
    try:
        subject = f"❌ Product Removed: {product_name[:30]}..."
        
        # ✅ NEW: Use Resend API
        resend.Emails.send({
            "from": "Naija Price Tracker <info@naija-price-tracker.name.ng>",
            "to": user_email,
            "subject": subject,
            "html": f"""
            <div style='font-family: Arial, sans-serif; max-width: 600px; margin: auto;'>
                <h3 style='color: #d9534f;'>Item Discontinued</h3>
                <p>The product <strong>{product_name}</strong> has been removed from Jumia (Dead Link).</p>
                <p>We have automatically removed it from your watchlist.</p>
            </div>
            """
        })
        print(f"   🗑️ Removal Email sent to {user_email}")
    except Exception as e:
        print(f"   ❌ Removal Email Failed: {e}")

# --- STOCK FINDER ---
def find_stock_status(soup):
    for hidden in soup(['script', 'style', 'meta', 'noscript']):
        hidden.decompose()
    stock_pattern = re.compile(r"((\d+|few)\s*(units|items)\s*left|in\s*stock|low\s*stock|out\s*of\s*stock)", re.IGNORECASE)
    stock_node = soup.find(string=stock_pattern)
    if stock_node: return stock_node.strip()
    if not stock_node:
        for tag in soup.find_all(class_=re.compile(r"(-rd5|-gy5)")):
            text = tag.get_text().strip()
            if text and len(text) < 30 and ("left" in text or "stock" in text.lower()):
                return text
    meter_div = soup.find("div", class_="meter")
    if meter_div and meter_div.parent:
        text_span = meter_div.parent.find("span")
        if text_span: return text_span.get_text().strip()
    return None

# --- RESTOCK FUNCTION ---
def restock_marketplace():
    from app import app, db, Product, PriceHistory
    print("\n🚚 RUNNING MARKET RESTOCK...")
    JUMIA_CATEGORY = "https://www.jumia.com.ng/mobile-phones/"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.google.com/"
    })
    
    with app.app_context():
        try:
            response = session.get(JUMIA_CATEGORY)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, "html.parser")
                cards = soup.find_all("article", class_="prd _fb col c-prd")
                added_count = 0
                for card in cards:
                    try:
                        link_tag = card.find("a", class_="core")
                        if not link_tag: continue
                        link = "https://www.jumia.com.ng" + link_tag.get("href")
                        if Product.query.filter_by(link=link).first(): continue
                        name = card.find("h3", class_="name").get_text()
                        price = 0.0
                        price_tag = card.find("div", class_="prc")
                        if price_tag:
                            clean = price_tag.get_text().strip().replace("₦", "").replace(",", "")
                            if "-" in clean: clean = clean.split("-")[0]
                            price = float(clean)
                        img_tag = card.find("img", class_="img")
                        image_url = img_tag.get("data-src") if img_tag else ""
                        if price > 0:
                            new_prod = Product(link=link, name=name, current_price=price, old_price=price, image_url=image_url, is_public=True)
                            db.session.add(new_prod)
                            db.session.commit()
                            db.session.add(PriceHistory(product_id=new_prod.id, price=price))
                            db.session.commit()
                            added_count += 1
                    except: continue
                print(f"🎉 RESTOCK COMPLETE: Added {added_count} new items.")
            else:
                print("❌ Restock failed: Jumia blocked connection.")
        except Exception as e:
            print(f"❌ Restock Error: {e}")

# --- MAIN LOOP ---
def start_bot():
    # 🚨 Import app logic HERE so it doesn't run when app.py first loads
    from app import app, db, Product, Tracking
    
    print("🤖 BOT WORKER STARTED! Checking for deals...")
    
    with app.app_context():
        if not Product.query.first():
            print("📭 Database is empty! Running INITIAL RESTOCK now...")
            restock_marketplace()

    hour_counter = 0
    
    while True:
        print("\n🔎 STARTING PRICE SCAN (SURGICAL MODE)...")
        
        with app.app_context():
            products = Product.query.all()
            print(f"📊 Tracking {len(products)} products.")
            
            session = requests.Session()
            session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"})

            for p in products:
                print(f"   👉 {p.name[:20]}... ", end='', flush=True)
                try:
                    time.sleep(random.uniform(MIN_WAIT, MAX_WAIT))
                    response = session.get(p.link, timeout=20, allow_redirects=True)
                    final_url = response.url
                    if "oos=1" in final_url or "out-of-stock" in final_url or not final_url.split("?")[0].endswith(".html"):
                        print(f"💀 DEAD LINK. Removing.")
                        for tracker in p.trackers:
                            send_product_removed_email(tracker.user.email, p.name, p.link)
                            db.session.delete(tracker)
                        p.is_public = False
                        db.session.commit()
                        continue

                    if response.status_code != 200:
                        print(f"⚠️ Status {response.status_code}")
                        continue

                    soup = BeautifulSoup(response.content, "html.parser")
                    found_price = 0.0
                    price_element = soup.select_one(".-fs24") 
                    if price_element:
                        try:
                            raw_text = price_element.get_text().strip()
                            clean_text = raw_text.replace("₦", "").replace(",", "").strip()
                            found_price = float(clean_text)
                        except: pass
                    
                    if found_price == 0.0:
                        meta_price = soup.find("meta", property="product:price:amount")
                        if meta_price: found_price = float(meta_price["content"])

                    if found_price == 0.0:
                        print("⚠️ No Price Found")
                        continue

                    stock_status = find_stock_status(soup)
                    if stock_status: p.stock_left = stock_status

                    if found_price < p.current_price:
                        print(f"📉 DROP! ₦{p.current_price:,.0f}->₦{found_price:,.0f}")
                        for tracker in p.trackers:
                            send_price_drop_email(tracker.user.email, p.name, found_price, p.current_price, p.link, p.image_url, stock_status)
                        p.old_price = p.current_price
                        p.current_price = found_price
                        db.session.commit()
                    elif found_price != p.current_price:
                        print(f"📈 UP ₦{p.current_price:,.0f} -> ₦{found_price:,.0f}")
                        p.old_price = p.current_price
                        p.current_price = found_price
                        db.session.commit()
                    else:
                        stock_msg = f"| {stock_status}" if stock_status else ""
                        print(f"✅ OK (₦{found_price:,.0f}) {stock_msg}")

                except Exception as e:
                    print(f"❌ {e}")
                    continue

        hour_counter += 1
        if hour_counter >= HOURS_UNTIL_RESTOCK:
            restock_marketplace()
            hour_counter = 0

        print("💤 Bot sleeping for 1 hour...")
        time.sleep(3600)
