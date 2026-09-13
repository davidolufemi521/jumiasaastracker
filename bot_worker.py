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

# --- RESTOCK FUNCTION (with 1000 cap + daily refresh) ---
def restock_marketplace():
    from app import app, db, Product, PriceHistory
    MARKETPLACE_CAP = 1000   # Hard limit — never exceed this
    REFRESH_TARGET  = 600    # Fill up to this many products

    # All categories — each scraped from page 1 AND page 2 for real variety
    BASE_CATEGORIES = [
        "https://www.jumia.com.ng/mobile-phones/",
        "https://www.jumia.com.ng/electronics/",
        "https://www.jumia.com.ng/computing/",
        "https://www.jumia.com.ng/category-fashion-by-jumia/",
        "https://www.jumia.com.ng/home-office/",
        "https://www.jumia.com.ng/health-beauty/",
        "https://www.jumia.com.ng/sporting-goods/",
        "https://www.jumia.com.ng/groceries/",
        "https://www.jumia.com.ng/baby-products/",
        "https://www.jumia.com.ng/garden-outdoors/",
        "https://www.jumia.com.ng/automotive/",
        "https://www.jumia.com.ng/books-movies-music/",
    ]
    # Build URLs for page 1 and page 2 of each category, shuffle for variety
    PAGES = []
    for base in BASE_CATEGORIES:
        PAGES.append(base + "?sort=newest")
        PAGES.append(base + "?page=2&sort=newest")
    random.shuffle(PAGES)

    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"})

    with app.app_context():
        try:
            current_count = Product.query.filter_by(is_public=True).count()
            print(f"\n🚚 MARKETPLACE RESTOCK | Current: {current_count} products")

            # --- DAILY REFRESH: if at/near cap, wipe and start fresh ---
            if current_count >= MARKETPLACE_CAP:
                print(f"♻️  Cap reached ({MARKETPLACE_CAP}). Wiping old marketplace items for daily refresh...")
                # Only delete public marketplace items (NOT user private tracked items)
                old_products = Product.query.filter_by(is_public=True).all()
                for p in old_products:
                    db.session.delete(p)
                db.session.commit()
                print(f"🗑️  Cleared {len(old_products)} old marketplace products.")
                current_count = 0

            # --- FILL UP to REFRESH_TARGET ---
            slots_available = REFRESH_TARGET - current_count
            if slots_available <= 0:
                print("✅ Marketplace is full enough. No restock needed.")
                return

            # Scrape all pages until slots are filled
            random.shuffle(PAGES)
            added_count = 0

            for category_url in PAGES:
                if added_count >= slots_available:
                    break
                print(f"   📦 Fetching from: {category_url.split('.ng/')[1].split('/?')[0]}")
                try:
                    response = session.get(category_url, timeout=15)
                    if response.status_code != 200:
                        print(f"   ⚠️ Got {response.status_code}, skipping.")
                        continue

                    soup = BeautifulSoup(response.content, "html.parser")
                    cards = soup.find_all("article", class_="prd _fb col c-prd")
                    if not cards:
                        # fallback selector
                        cards = soup.find_all("article", class_="prd")

                    for card in cards:
                        if added_count >= slots_available:
                            break
                        try:
                            link_tag = card.find("a", class_="core")
                            if not link_tag: continue
                            link = "https://www.jumia.com.ng" + link_tag.get("href")
                            if Product.query.filter_by(link=link).first(): continue

                            name_tag = card.find("h3", class_="name")
                            price_tag = card.find("div", class_="prc")
                            img_tag  = card.find("img", class_="img")
                            if not (name_tag and price_tag): continue

                            name  = name_tag.get_text().strip()[:490]
                            clean = price_tag.get_text().strip().replace("₦", "").replace(",", "").split("-")[0]
                            price = float(clean)
                            image_url = img_tag.get("data-src") if img_tag else ""

                            if price > 0 and image_url:   # only add if we have both price AND image
                                new_prod = Product(
                                    link=link, name=name,
                                    current_price=price, old_price=price,
                                    image_url=image_url, stock_left="In stock",
                                    is_public=True
                                )
                                db.session.add(new_prod)
                                db.session.flush()
                                db.session.add(PriceHistory(product_id=new_prod.id, price=price))
                                added_count += 1
                        except: continue

                    db.session.commit()
                except Exception as e:
                    print(f"   ❌ Category error: {e}")
                    db.session.rollback()
                    continue

            final_count = Product.query.filter_by(is_public=True).count()
            print(f"🎉 RESTOCK DONE: Added {added_count} | Total now: {final_count}/{MARKETPLACE_CAP}")

        except Exception as e:
            db.session.rollback()
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

    while True:
        print("\n🔎 STARTING PRICE SCAN (SURGICAL MODE)...")
        
        with app.app_context():
            products = Product.query.all()
            print(f"📊 Tracking {len(products)} products.")
            
            import requests
            session = requests.Session()
            session.headers.update({"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"})

            for p in products:
                print(f"   👉 {p.name[:20]}... ", end='', flush=True)
                try:
                    time.sleep(random.uniform(MIN_WAIT, MAX_WAIT))
                    response = session.get(p.link, timeout=20, allow_redirects=True)
                    final_url = response.url
                    if "oos=1" in final_url or "out-of-stock" in final_url:
                        print(f"💀 DEAD LINK. Removing.")
                        for tracker in p.trackers:
                            send_product_removed_email(tracker.user.email, p.name, p.link)
                            db.session.delete(tracker)
                        if p.is_public:
                            # Marketplace product — just delete it entirely
                            db.session.delete(p)
                        else:
                            # User's private item — keep but mark hidden
                            p.stock_left = "Out of Stock"
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

        # Run restock every cycle (every 16 hours)
        restock_marketplace()

        print("💤 Bot sleeping for 16 hours...")
        time.sleep(16 * 3600)
