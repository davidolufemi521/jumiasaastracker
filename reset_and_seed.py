import os
import requests
import time
import random
from bs4 import BeautifulSoup
from app import app, db, Product, PriceHistory

def reset_and_seed():
    print("⚠️  INITIATING DATABASE RESET (DEPLOYMENT READY)...")
    
    # 1. SMART WIPE (Works for both SQLite and PostgreSQL)
    with app.app_context():
        try:
            print("🗑️  Dropping old tables...")
            db.drop_all()  # This deletes all tables in the connected DB
            print("✅  Tables dropped.")
            
            print("🔨  Creating new tables...")
            db.create_all() # This creates fresh tables
            print("✅  New Database Ready.")
        except Exception as e:
            print(f"❌ Database Error: {e}")
            return

        print("🚀 Starting Bulk Scrape (Pages 1-5)...")
        
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        })

        base_url = "https://www.jumia.com.ng/mobile-phones/?page={}"
        count = 0
        
        # INCREASED TO 5 PAGES
        for page in range(1, 6):
            url = base_url.format(page)
            print(f"--- Scraping Page {page} ---")
            
            try:
                time.sleep(1.5) 
                response = session.get(url)
                soup = BeautifulSoup(response.content, "html.parser")
                
                # Find all products (Generic 'prd' class)
                articles = soup.find_all("article", class_="prd")
                
                if not articles:
                    print(f"   ⚠️ No items found on Page {page}. Jumia might be blocking.")
                    continue

                print(f"   Found {len(articles)} potential items...")

                items_added_on_page = 0
                
                for article in articles:
                    try:
                        # 1. LINK (Required)
                        link_tag = article.find("a", class_="core")
                        if not link_tag: continue
                        link = "https://www.jumia.com.ng" + link_tag.get("href")
                        
                        # 2. NAME (Required)
                        name_tag = article.find("h3", class_="name")
                        if not name_tag: continue
                        name = name_tag.get_text().strip()
                        
                        # 3. PRICE (Required)
                        price_tag = article.find("div", class_="prc")
                        if not price_tag: continue
                        price_clean = price_tag.get_text().strip().replace("₦", "").replace(",", "")
                        if "-" in price_clean: price_clean = price_clean.split("-")[0]
                        price = float(price_clean.strip())
                        
                        # 4. IMAGE (Optional - Use Placeholder if missing)
                        img_tag = article.find("img", class_="img")
                        if img_tag:
                            image_url = img_tag.get("data-src") or img_tag.get("src")
                        else:
                            # Placeholder image so we don't skip the item
                            image_url = "https://via.placeholder.com/300?text=No+Image"

                        # 5. DUPLICATE CHECK
                        exists = Product.query.filter_by(link=link).first()
                        if not exists:
                            new_prod = Product(
                                link=link, name=name, current_price=price, 
                                old_price=price, image_url=image_url, 
                                is_public=True 
                            )
                            db.session.add(new_prod)
                            db.session.commit()
                            
                            hist = PriceHistory(product_id=new_prod.id, price=price)
                            db.session.add(hist)
                            db.session.commit()
                            
                            count += 1
                            items_added_on_page += 1
                            # Only print every 5th item to keep terminal clean
                            if count % 5 == 0:
                                print(f"   ✅ Added: {name[:20]}... (Total: {count})")
                        else:
                            # Silently skip duplicates to keep log clean
                            pass
                            
                    except Exception: continue
                
                print(f"   -> Added {items_added_on_page} new items from Page {page}.")

            except Exception as e:
                print(f"Error on page {page}: {e}")

    print(f"\n🎉 FINAL TOTAL: Added {count} products. Now run 'python app.py'")

if __name__ == "__main__":
    reset_and_seed()
