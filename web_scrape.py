from flask import Flask, request, render_template, jsonify, send_from_directory
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.common.keys import Keys
import threading
import time
import re
import uuid
import csv
import os

FIREFOX_DRIVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "geckodriver")

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

progress_dict = {}
progress_lock = threading.Lock()

def selenium_scrape(url, data_types, follow_pagination, login_info, progress_callback):
    options = FirefoxOptions()
    options.headless = True
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument("--width=1920")
    options.add_argument("--height=1080")

    service = FirefoxService(executable_path=FIREFOX_DRIVER_PATH)
    driver = webdriver.Firefox(service=service, options=options)

    collected_text = ""
    current_url = url
    page_count = 0
    wait = WebDriverWait(driver, 10)

    try:
        driver.get(current_url)

        # Optional Login
        if login_info and login_info['username'] and login_info['password']:
            try:
                user_input = driver.find_element(By.NAME, login_info['username_field'])
                pass_input = driver.find_element(By.NAME, login_info['password_field'])
                user_input.send_keys(login_info['username'])
                pass_input.send_keys(login_info['password'])
                pass_input.send_keys(Keys.RETURN)
                time.sleep(3)
                progress_callback("Logged in successfully.")
            except Exception as e:
                progress_callback(f"Login failed: {str(e)}")

        while current_url:
            driver.get(current_url)
            wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, 'body')))

            page_source = driver.page_source
            collected_text += page_source
            page_count += 1

            progress_callback(f"Scraped page {page_count}: {current_url}")

            if follow_pagination:
                next_url = None
                try:
                    next_button = driver.find_element(By.LINK_TEXT, 'Next')
                    next_url = next_button.get_attribute('href')
                except:
                    try:
                        next_link = driver.find_element(By.CSS_SELECTOR, 'a[rel="next"]')
                        next_url = next_link.get_attribute('href')
                    except:
                        next_url = None

                if next_url and next_url != current_url:
                    current_url = next_url
                else:
                    break
            else:
                break
    finally:
        driver.quit()

    results = {}

    def flatten(matches):
        return list(set([m.strip() if isinstance(m, str) else ''.join(m).strip() for m in matches]))

    if "phone" in data_types:
        pattern = r'(\+?\d[\d\s\-()]{6,14}\d)'
        results['phone'] = flatten(re.findall(pattern, collected_text))

    if "dob" in data_types:
        pattern = r'\b(0[1-9]|[12][0-9]|3[01])[-/.](0[1-9]|1[012])[-/.](19|20)\d\d\b'
        results['dob'] = flatten(re.findall(pattern, collected_text))

    if "email" in data_types:
        pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        results['email'] = flatten(re.findall(pattern, collected_text))

    if "name" in data_types:
        pattern = r'\b[A-Z][a-z]{1,}\s[A-Z][a-z]{1,}\b'
        results['name'] = flatten(re.findall(pattern, collected_text))

    if "address" in data_types:
        pattern = r'\d{1,5}\s\w+\s\w+'
        results['address'] = flatten(re.findall(pattern, collected_text))

    if "purchase_id" in data_types:
        pattern = r'\b[A-Za-z0-9]{6,12}\b'
        results['purchase_id'] = flatten(re.findall(pattern, collected_text))

    # Extra fields
    if "username" in data_types:
        pattern = r'username["\'>:\s]+([a-zA-Z0-9._-]{4,})'
        results['username'] = flatten(re.findall(pattern, collected_text))

    if "transaction_id" in data_types:
        pattern = r'(TXN[\-_]?[0-9A-Za-z]{6,})'
        results['transaction_id'] = flatten(re.findall(pattern, collected_text))

    if "payout" in data_types:
        pattern = r'(\$\s?\d{1,3}(,\d{3})*(\.\d{2})?)'
        results['payout'] = flatten(re.findall(pattern, collected_text))

    return results

@app.route('/')
def index():
    return render_template("scrape_form.html")

@app.route('/scrape-start', methods=['POST'])
def scrape_start():
    url = request.form.get('website_url')
    data_types = request.form.getlist('data_type')
    follow_pagination = request.form.get('follow_pagination') == 'on'

    login_info = {
        'username': request.form.get('login_username'),
        'password': request.form.get('login_password'),
        'username_field': request.form.get('login_user_field', 'username'),
        'password_field': request.form.get('login_pass_field', 'password')
    }

    if not url or not data_types:
        return jsonify({"error": "URL and at least one data type are required"}), 400

    scrape_id = str(uuid.uuid4())
    with progress_lock:
        progress_dict[scrape_id] = "Starting scrape..."

    def progress_cb(msg):
        with progress_lock:
            progress_dict[scrape_id] = msg

    def run_scrape():
        try:
            results = selenium_scrape(url, data_types, follow_pagination, login_info, progress_cb)
            csv_file = os.path.join(app.config['UPLOAD_FOLDER'], f"scrape_results_{scrape_id}.csv")
            with open(csv_file, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Data Type', 'Value'])
                for key, values in results.items():
                    for val in values:
                        writer.writerow([key, val])
            with progress_lock:
                progress_dict[scrape_id] = f"Completed! <a href='/download/{scrape_id}'>Download CSV</a>"
        except Exception as e:
            with progress_lock:
                progress_dict[scrape_id] = f"Error: {str(e)}"

    threading.Thread(target=run_scrape).start()
    return jsonify({"scrape_id": scrape_id})

@app.route('/scrape-progress/<scrape_id>')
def scrape_progress(scrape_id):
    with progress_lock:
        msg = progress_dict.get(scrape_id, "No progress info found.")
    return jsonify({"progress": msg})

@app.route('/download/<scrape_id>')
def download(scrape_id):
    filename = f"scrape_results_{scrape_id}.csv"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(filepath):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)
    else:
        return "File not found", 404

if __name__ == "__main__":
    app.run(debug=True)
