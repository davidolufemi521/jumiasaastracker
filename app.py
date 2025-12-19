import os
import threading
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from sqlalchemy import desc
import random
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta

# 🚨 IMPORT YOUR BOT (Safely)
# This imports the start_bot function from bot_worker.py
try:
    from bot_worker import start_bot
except ImportError:
    print("⚠️ WARNING: Could not import start_bot from bot_worker. Bot will not run.")
    def start_bot(): pass

print("✅✅ I AM THE CORRECT FILE! I AM LOADING! ✅✅")

# --- CONFIGURATION ---
app = Flask(__name__)
# Get Secret Key from Environment (Render) or use default (Laptop)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'my_secret_key_change_this')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 🚨 SMART DATABASE SWITCH (RENDER vs LAPTOP) 🚨
database_url = os.environ.get('DATABASE_URL')

if database_url:
    # We are on Render (Use PostgreSQL)
    # Fix Render's URL format (postgres:// -> postgresql://)
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # We are on Laptop (Use SQLite)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///jumia_saas.db'

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = os.environ.get('EMAIL_USER')
app.config['MAIL_PASSWORD'] = os.environ.get('EMAIL_PASS')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('EMAIL_USER')

mail = Mail(app)

# --- 🛡️ DATABASE STABILITY FIX (REQUIRED) ---
# Keep this to prevent that "SSL Decryption Failed" error you saw earlier
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

db = SQLAlchemy(app)

# 🚨 FIX FOR DATABASE LOCKED ERROR (Only needed for SQLite on Laptop) 🚨
if not database_url:
    with app.app_context():
        from sqlalchemy import text
        try:
            db.session.execute(text("PRAGMA journal_mode=WAL;"))
            db.session.commit()
            print("✅ Database WAL mode enabled (Fixes locking issues).")
        except Exception as e:
            print(f"⚠️ Note: Could not set WAL mode: {e}")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    otp_code = db.Column(db.String(6), nullable=True)
    otp_expiry = db.Column(db.DateTime, nullable=True)
    is_verified = db.Column(db.Boolean, default=False)
    login_attempts = db.Column(db.Integer, default=0)
    tracked_products = db.relationship('Tracking', backref='user', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    link = db.Column(db.String(500), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    current_price = db.Column(db.Float, nullable=False)
    old_price = db.Column(db.Float, nullable=True)
    image_url = db.Column(db.String(500), nullable=True)
    stock_left = db.Column(db.String(50), nullable=True)
    is_public = db.Column(db.Boolean, default=False)
    trackers = db.relationship('Tracking', backref='product', lazy=True)
    price_history = db.relationship('PriceHistory', backref='product', lazy=True)

class PriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    price = db.Column(db.Float, nullable=False)
    date_recorded = db.Column(db.DateTime, default=datetime.utcnow)

class Tracking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False)
    date_sent = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ADMIN SECURITY DECORATOR ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.email != "davidolufemi773@gmail.com": 
            flash("❌ You are not the Admin!", "danger")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# --- SCRAPER FUNCTION (UPDATED) ---
def scrape_jumia_product(url):
    if "jumia.com.ng" not in url:
        print("❌ Rejected: Not a Jumia link")
        return None
        
    clean_url = url.split("?")[0]
    if not clean_url.endswith(".html"):
        print("❌ Rejected: Category link")
        return None

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Referer": "https://www.google.com/"
    })

    try:
        response = session.get(url, timeout=10)
        if response.status_code != 200: return None
        
        soup = BeautifulSoup(response.content, "html.parser")
        
        # 1. NAME
        name = "Unknown Product"
        meta_title = soup.find("meta", property="og:title")
        if meta_title: name = meta_title["content"]
        elif soup.title: name = soup.title.string

        # 2. IMAGE
        image_url = "https://via.placeholder.com/300?text=No+Image"
        meta_image = soup.find("meta", property="og:image")
        if meta_image: image_url = meta_image["content"]

        # 3. PRICE
        price = 0.0
        price_element = soup.select_one(".-fs24") # Look for bold price
        if price_element:
            try:
                raw_text = price_element.get_text().strip()
                clean_text = raw_text.replace("₦", "").replace(",", "").strip()
                price = float(clean_text)
            except: pass
        
        if price == 0.0:
            meta_price = soup.find("meta", property="product:price:amount")
            if meta_price:
                try: price = float(meta_price["content"])
                except: pass

        if price == 0.0: return None 

        # 4. STOCK STATUS
        stock_text = None
        for hidden in soup(['script', 'style', 'meta', 'noscript']):
            hidden.decompose()

        stock_pattern = re.compile(r"((\d+|few)\s*(units|items)\s*left|in\s*stock|low\s*stock|out\s*of\s*stock)", re.IGNORECASE)
        stock_node = soup.find(string=stock_pattern)
        if stock_node:
            stock_text = stock_node.strip()

        if not stock_text:
            for tag in soup.find_all(class_=re.compile(r"(-rd5|-gy5)")):
                text = tag.get_text().strip()
                if text and len(text) < 30 and ("left" in text or "stock" in text.lower()):
                    stock_text = text
                    break
        
        if not stock_text:
            meter_div = soup.find("div", class_="meter")
            if meter_div and meter_div.parent:
                text_span = meter_div.parent.find("span")
                if text_span: 
                    stock_text = text_span.get_text().strip()

        return {"name": name, "price": price, "image": image_url, "stock": stock_text}
        
    except Exception as e:
        print(f"Scrape Error: {e}")
        return None

# --- ROUTES ---

@app.route('/')
def index():
    if current_user.is_authenticated: return redirect(url_for('home'))
    ticker_deals = Product.query.filter(Product.is_public == True, Product.current_price < Product.old_price).limit(5).all()
    return render_template('landing.html', ticker_deals=ticker_deals)

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/dashboard')
@login_required
def home():
    market_deals = Product.query.filter(
        Product.is_public == True, 
        Product.current_price < Product.old_price
    ).order_by(
        desc(Product.old_price - Product.current_price)
    ).limit(6).all()
    
    my_alerts = [t.product for t in Tracking.query.filter_by(user_id=current_user.id).all() if t.product.current_price < t.product.old_price]
    
    return render_template('home.html', name=current_user.name, market_deals=market_deals, my_alerts=my_alerts)

@app.route('/watchlist')
@login_required
def watchlist():
    return render_template('watchlist.html', my_tracking=Tracking.query.filter_by(user_id=current_user.id).all())

@app.route('/marketplace', methods=['GET', 'POST'])
@login_required
def marketplace():
    if request.method == 'POST':
        p_id = request.form.get('product_id')
        if not Tracking.query.filter_by(user_id=current_user.id, product_id=p_id).first():
            db.session.add(Tracking(user_id=current_user.id, product_id=p_id))
            db.session.commit()
            flash('Added to Watchlist!', 'success')
        else: flash('Already tracking.', 'warning')
        return redirect(url_for('marketplace'))
    return render_template('marketplace.html', all_products=Product.query.filter_by(is_public=True).all())

@app.route('/track_item/<int:product_id>', methods=['POST'])
@login_required
def track_item(product_id):
    if not Tracking.query.filter_by(user_id=current_user.id, product_id=product_id).first():
        db.session.add(Tracking(user_id=current_user.id, product_id=product_id))
        db.session.commit()
        flash('Added to Watchlist!', 'success')
    else:
        flash('You are already tracking this item.', 'info')
    return redirect(request.referrer or url_for('home'))

@app.route('/add_link', methods=['GET', 'POST'])
@login_required
def add_link():
    preview_data = None
    if request.method == 'POST':
        if 'link' in request.form:
            link = request.form.get('link')
            existing = Product.query.filter_by(link=link).first()
            if existing: 
                preview_data = { 
                    "name": existing.name, 
                    "price": existing.current_price, 
                    "image": existing.image_url, 
                    "link": link, 
                    "stock": existing.stock_left,
                    "exists": True 
                }
            else:
                data = scrape_jumia_product(link)
                if data: 
                    preview_data = {**data, 'link': link, 'exists': False}
                else: flash('Could not fetch product.', 'danger')
        elif 'confirm_track' in request.form:
            link = request.form.get('confirm_link')
            prod = Product.query.filter_by(link=link).first()
            if not prod:
                prod = Product(
                    link=link, 
                    name=request.form.get('confirm_name'), 
                    current_price=float(request.form.get('confirm_price')), 
                    old_price=float(request.form.get('confirm_price')), 
                    image_url=request.form.get('confirm_image'),
                    stock_left=request.form.get('confirm_stock'),
                    is_public=False
                )
                db.session.add(prod)
                db.session.commit()
                db.session.add(PriceHistory(product_id=prod.id, price=prod.current_price))
                db.session.commit()
            if not Tracking.query.filter_by(user_id=current_user.id, product_id=prod.id).first():
                db.session.add(Tracking(user_id=current_user.id, product_id=prod.id))
                db.session.commit()
                flash('Tracked!', 'success')
            else: flash('Already tracking.', 'warning')
            return redirect(url_for('watchlist'))
    return render_template('add_link.html', preview=preview_data)

@app.route('/delete_tracking/<int:track_id>')
@login_required
def delete_tracking(track_id):
    track = Tracking.query.get(track_id)
    if track and track.user_id == current_user.id:
        db.session.delete(track)
        db.session.commit()
        flash('Removed.', 'info')
    return redirect(url_for('watchlist'))

# --- ADMIN ROUTES ---

@app.route('/contact', methods=['GET', 'POST'])
@login_required
def contact():
    if request.method == 'POST':
        msg = request.form.get('message')
        if msg:
            new_feedback = Feedback(user_email=current_user.email, message=msg)
            db.session.add(new_feedback)
            db.session.commit()
            flash("✅ Feedback sent! We will read it shortly.", "success")
            return redirect(url_for('home'))
    return render_template('contact.html', name=current_user.name)

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    total_users = User.query.count()
    total_products = Product.query.count()
    all_feedbacks = Feedback.query.order_by(Feedback.date_sent.desc()).all()
    all_users = User.query.all()
    
    return render_template('admin.html', 
                           users=all_users, 
                           feedbacks=all_feedbacks, 
                           user_count=total_users, 
                           product_count=total_products)

@app.route('/delete_feedback/<int:id>')
@login_required
@admin_required
def delete_feedback(id):
    f = Feedback.query.get(id)
    if f:
        db.session.delete(f)
        db.session.commit()
        flash("🗑️ Feedback deleted.", "success")
    return redirect(url_for('admin_dashboard'))

# --- AUTH ROUTES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        if user:
            if check_password_hash(user.password, password):
                user.login_attempts = 0
                db.session.commit()
                if user.is_verified:
                    login_user(user)
                    return redirect(url_for('home'))
                else:
                    flash('Verify email first.', 'warning')
                    session['email_to_verify'] = email
                    return redirect(url_for('verify_otp'))
            else:
                user.login_attempts += 1
                db.session.commit()
                if user.login_attempts >= 4:
                    flash('Too many attempts. Did you forget your password?', 'danger')
                    return redirect(url_for('forgot_password'))
                else:
                    flash(f'Invalid Password. Attempt {user.login_attempts}/4', 'danger')
        else: flash('Email not found.', 'danger')
    return render_template('login.html')

# --- 1. ADD THIS HELPER FUNCTION OUTSIDE THE ROUTE ---
def send_async_email(app, recipient, otp_code):
    """Sends email in the background to prevent server crashing."""
    with app.app_context():
        try:
            print(f"⏳ Background: Sending OTP to {recipient}...", flush=True)
            msg = Message('Verify Account', sender=app.config['MAIL_USERNAME'], recipients=[recipient])
            msg.body = f"Your Verification Code is: {otp_code}"
            mail.send(msg)
            print(f"✅ Background: Email sent successfully to {recipient}!", flush=True)
        except Exception as e:
            print(f"❌ Background Email Failed: {str(e)}", flush=True)

# --- 2. YOUR ORIGINAL REGISTER ROUTE (WITH THREADING ADDED) ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email').strip().lower()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        # [KEEPING YOUR REGEX CHECK]
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_regex, email):
            flash('Invalid email address format.', 'danger')
            return redirect(url_for('register'))

        # [KEEPING YOUR TYPO CHECK]
        domain = email.split('@')[-1]
        common_typos = {
            "gmil.com": "gmail.com", "gmal.com": "gmail.com", "gmaill.com": "gmail.com",
            "yaho.com": "yahoo.com", "yahooo.com": "yahoo.com", "outlok.com": "outlook.com"
        }
        if domain in common_typos:
            correct = common_typos[domain]
            flash(f"Did you mean '{correct}'? You typed '{domain}'.", 'warning')
            return redirect(url_for('register'))
        
        if password != confirm_password:
            flash('Passwords do not match!', 'danger')
            return redirect(url_for('register'))

        # Generate OTP
        otp = str(random.randint(100000, 999999))
        expiry = datetime.utcnow() + timedelta(minutes=20)
        
        # Always print OTP to logs for safety
        print(f"🔥🔥🔥 NEW OTP GENERATED: {otp} 🔥🔥🔥", flush=True) 

        user = User.query.filter_by(email=email).first()
        
        if user:
            # [CASE A: USER EXISTS BUT NOT VERIFIED]
            if not user.is_verified:
                user.name = name
                user.password = generate_password_hash(password, method='pbkdf2:sha256')
                user.otp_code = otp
                user.otp_expiry = expiry
                db.session.commit()
                
                # ⚡ THREADED EMAIL (Non-Blocking)
                threading.Thread(target=send_async_email, args=(app._get_current_object(), email, otp)).start()
                
                session['email_to_verify'] = email
                flash('Account found but not verified. Sending new code...', 'info')
                return redirect(url_for('verify_otp'))
            else:
                # [CASE B: USER ALREADY VERIFIED]
                flash('Email already registered. Please Login.', 'warning')
                return redirect(url_for('login'))
        else:
            # [CASE C: NEW USER]
            new_user = User(name=name, email=email, password=generate_password_hash(password, method='pbkdf2:sha256'), otp_code=otp, otp_expiry=expiry)
            db.session.add(new_user)
            db.session.commit()
            
            # ⚡ THREADED EMAIL (Non-Blocking)
            threading.Thread(target=send_async_email, args=(app._get_current_object(), email, otp)).start()
            
            session['email_to_verify'] = email
            return redirect(url_for('verify_otp'))
            
    return render_template('register.html')

@app.route('/verify', methods=['GET', 'POST'])
def verify_otp():
    if request.method == 'POST':
        entered_otp = request.form.get('otp')
        email = session.get('email_to_verify')
        user = User.query.filter_by(email=email).first()
        if user and user.otp_code == entered_otp:
            user.is_verified = True
            db.session.commit()
            return render_template('success.html')
        flash('Invalid Code', 'danger')
    return render_template('verify.html')

@app.route('/resend_otp')
def resend_otp():
    if 'email_to_verify' not in session:
        flash('Session expired. Please login again.', 'danger')
        return redirect(url_for('login'))
    
    email = session['email_to_verify']
    user = User.query.filter_by(email=email).first()
    
    if user:
        otp = str(random.randint(100000, 999999))
        user.otp_code = otp
        user.otp_expiry = datetime.utcnow() + timedelta(minutes=20)
        db.session.commit()
        
        try:
            msg = Message('New Verification Code', sender=app.config['MAIL_USERNAME'], recipients=[email])
            msg.body = f"Your New Code is: {otp}"
            mail.send(msg)
            flash('✅ New code sent! Check your email.', 'success')
        except Exception as e:
            flash(f'Error sending email: {e}', 'danger')
    return redirect(url_for('verify_otp'))

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            otp = str(random.randint(100000, 999999))
            user.otp_code = otp
            user.otp_expiry = datetime.utcnow() + timedelta(minutes=10)
            db.session.commit()
            try:
                msg = Message('Password Reset Code', sender=app.config['MAIL_USERNAME'], recipients=[email])
                msg.body = f"Your Password Reset Code is: {otp}"
                mail.send(msg)
                session['email_reset'] = email
                flash('OTP sent to your email.', 'info')
                return redirect(url_for('reset_password'))
            except: flash('Error sending email.', 'danger')
        else:
            flash('Email not found.', 'warning')
    return render_template('forgot_password.html')

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        otp = request.form.get('otp')
        new_pass = request.form.get('password')
        confirm_pass = request.form.get('confirm_password')
        email = session.get('email_reset')
        
        if new_pass != confirm_pass:
            flash('Passwords do not match!', 'danger')
            return redirect(url_for('reset_password'))
            
        user = User.query.filter_by(email=email).first()
        if user and user.otp_code == otp:
            if datetime.utcnow() < user.otp_expiry:
                user.password = generate_password_hash(new_pass, method='pbkdf2:sha256')
                user.otp_code = None
                user.login_attempts = 0
                db.session.commit()
                flash('Password changed! Please Login.', 'success')
                return redirect(url_for('login'))
            else: flash('Code expired.', 'danger')
        else: flash('Invalid Code.', 'danger')
    return render_template('reset_password.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/feedback', methods=['POST'])
def submit_public_feedback():
    name = request.form.get('name')
    email = request.form.get('email')
    msg = request.form.get('message')

    if msg:
        sender = email if email else "Anonymous"
        full_message = f"Name: {name}\n\n{msg}"
        new_feedback = Feedback(user_email=sender, message=full_message)
        db.session.add(new_feedback)
        db.session.commit()
        flash("✅ Message received! We'll be in touch.", "success")
    else:
        flash("❌ Message cannot be empty.", "warning")
    return redirect(url_for('index'))
# --- 🛠️ DEPLOYMENT HELPER ROUTE ---
@app.route('/deploy-fix')
def deploy_fix():
    # 1. Create Tables
    with app.app_context():
        db.create_all()
    
    # 2. Scrape Jumia (Page 1 Only)
    try:
        import requests
        from bs4 import BeautifulSoup
        
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        url = "https://www.jumia.com.ng/mobile-phones/"
        
        response = session.get(url)
        soup = BeautifulSoup(response.content, "html.parser")
        articles = soup.find_all("article", class_="prd")
        
        count = 0
        for article in articles:
            try:
                link_tag = article.find("a", class_="core")
                name_tag = article.find("h3", class_="name")
                price_tag = article.find("div", class_="prc")
                
                if link_tag and name_tag and price_tag:
                    link = "https://www.jumia.com.ng" + link_tag.get("href")
                    name = name_tag.get_text().strip()
                    price_clean = price_tag.get_text().strip().replace("₦", "").replace(",", "").split("-")[0]
                    price = float(price_clean)
                    
                    if not Product.query.filter_by(link=link).first():
                        new_prod = Product(link=link, name=name, current_price=price, old_price=price, is_public=True)
                        db.session.add(new_prod)
                        
                        # 🚨 THIS IS THE FIX: Force DB to generate the ID now
                        db.session.flush() 
                        
                        # Now new_prod.id exists!
                        db.session.add(PriceHistory(product_id=new_prod.id, price=price))
                        count += 1
            except: continue
            
        db.session.commit()
        return f"✅ SUCCESS! Database Created & Seeded with {count} items. <a href='/dashboard'>Go to Dashboard</a>"
        
    except Exception as e:
        db.session.rollback() # Reset if error happens
        return f"❌ ERROR: {str(e)}"

# --- 🚀 MERGED RUNNER (WEBSITE + BOT) ---
def run_bot_in_background():
    # This runs the bot loop in a separate "thread"
    with app.app_context():
        start_bot()

# 🚨 CRITICAL DEPLOYMENT FIX 🚨
# 1. RENDER (GUNICORN) STARTUP
# Gunicorn doesn't run __main__, so we check if we are on Render.
if os.environ.get("RENDER"):
    # Ensure we don't start multiple threads if Gunicorn has multiple workers (Safety check)
    if not any(t.name == "JumiaBotThread" for t in threading.enumerate()):
        t = threading.Thread(target=run_bot_in_background, name="JumiaBotThread", daemon=True)
        t.start()
        print("🚀 Render Detected: Bot Started!")

# 2. LOCAL STARTUP
if __name__ == '__main__':
    # Start bot locally
    t = threading.Thread(target=run_bot_in_background, name="JumiaBotThread", daemon=True)
    t.start()
    print("🚀 Local: Bot Started!")
    
    with app.app_context(): db.create_all()
    # use_reloader=False prevents the bot from starting twice

    app.run(debug=True, port=5001, use_reloader=False)





