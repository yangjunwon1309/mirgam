from flask import Flask, render_template, url_for, redirect, request
from flask import flash, session
import csv
import pandas as pd
import shutil
import datetime
import os, getpass
import logging
import json

app = Flask(__name__)
#app.config["config_key"] = config_value
UPLOAD_FOLDER = 'apps/static'  # 여기서 'app/static'은 실제 경로에 맞게 수정해야 합니다.
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config["SECRET_KEY"] = "2AZMss3p5QPbcY2hBsJ"
app.logger.setLevel(logging.DEBUG)
#app.logger.error("error")

app.config["DEBUG_TB_INTERCEPT_REDIRECTS"] = False
#toolbar = DebugToolbarExtension(app)





def read_csv_file(filepath):
    return pd.read_csv(filepath)

def write_csv_file(filepath, data):
    with open(filepath, 'w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerows(data)

@app.get('/')
def init():
    session["login_path"] = os.path.join(app.static_folder, 'login.csv')
    session["customer_path"] = os.path.join(app.static_folder, 'customer.csv')
    session["order_path"] = os.path.join(app.static_folder, 'order.csv')
    session["items_path"] = os.path.join(app.static_folder, 'items.csv')
    session["customer_upload_path"] = os.path.join(app.static_folder, 'customer_upload.csv')
    return redirect(url_for("login"))

@app.get("/index/<ex>", endpoint = "hello-endpoint")
def index(ex):
    return f"Hello {ex}"

@app.route("/contact")
def contact():
    return render_template("contact.html")

@app.route('/login', methods = ['GET'])
def login_in():
    return render_template("login.html")

@app.route('/login', methods=['POST'])
def login():
    login_path =  session["login_path"]
    login_pd = pd.read_csv(login_path)
    username = request.form["farm-name"]
    pw = request.form["password"]

    # 사용자 입력 정보 확인
    is_valid = False
    if not username:
        flash("이름 입력은 필수입니다")
    elif not pw:
        flash("비밀번호 입력은 필수입니다")
    else:
        # 사용자 이름과 비밀번호가 모두 일치하는지 확인
        user = login_pd[(login_pd['name'] == str(username)) & (login_pd['pw'] == int(pw))]
        if not user.empty:
            is_valid = True
            flash("입력해주셔서 감사합니다")
        else:
            flash("농장 이름 혹은 비밀번호가 틀렸습니다")

    if is_valid:
        directory = "static"
        filenameList = [f"order_{username}.csv", f'customer_{username}.csv', f'customer_upload_{username}.csv', 'items.csv']
        filepathList = [os.path.join(directory, filename) for filename in filenameList]
        for filepath in filepathList:
            directory = os.path.dirname(filepath)
            # Check if the directory exists, if not, create it
            if not os.path.exists(directory):
                os.makedirs(directory)
            if not os.path.isfile(filepath):
                with open(filepath, 'w') as file:
                    if filepathList.index(filepath) == 0 :
                        file.write("name,ph,address,item,quantity,date\n")
                    elif filepathList.index(filepath) < 3:
                        file.write("name,ph,address\n")
                    else :
                        file.write("item,price\n")

        session["customer_path"] = os.path.join(app.static_folder, f'customer_{username}.csv')
        session["order_path"] = os.path.join(app.static_folder, f'order_{username}.csv')
        session["item_path"] = os.path.join(app.static_folder, 'items.csv')
        session["customer_upload_path"] = os.path.join(app.static_folder, f'customer_upload_{username}.csv')

        return redirect(url_for("order"))
    else:
        return redirect(url_for("login"))


@app.route("/order", methods = ['GET'])
def order():
    customer_upload_path = session["customer_upload_path"]
    items_path = session["items_path"]

    #customers_pd = pd.read_csv(customer_path)
    customers_pd = pd.read_csv(customer_upload_path)
    expected_columns = ["name","ph","address"]
    if not all(column in customers_pd.columns for column in expected_columns):
        flash("알맞지 않은 형식의 고객 파일입니다")
        flash("엑셀 파일의 열을 name, ph, address로 반드시 맞춰주세요!")
        return redirect(url_for("mypage"))
    #customers_pd = pd.concat([customers_pd, customers_upload_pd], ignore_index=True)
    customers_d = customers_pd.to_dict(orient='records')
    items_pd = pd.read_csv(items_path)
    items_d = items_pd.to_dict(orient='records')
    return render_template("order.html", customers = customers_d, items = items_d)

@app.route("/order", methods = ['POST'])
def order_new():
    #customer_upload_path = session["customer_upload_path"]
    #items_path = session["items_path"]
    order_path = session["order_path"]

    users = request.form["users"]
    print(users)
    users = json.loads(users)
    print(users)
    name = request.form['selected_name']
    #ph = request.form['selected_phone']
    #address = request.form['selected_address']

    quant = request.form['quantity']
    item = request.form['item_name']

    date = datetime.datetime.now()

    #date = current_date.strftime("%Y-%m-%d")
    #print(name, ph, quant, item)

    with open(order_path, 'a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        for name in users.keys():
            ph = users[name]["phone"]
            address = users[name]["address"]
            writer.writerows([[name, ph, address, item, quant, date]])

    if '_flashes' in session:
        del session['_flashes']
    flash('정상 처리 되었습니다')
    return redirect(url_for('order'))

@app.route('/order_view')
def order_view():
    #customer_upload_path = session["customer_upload_path"]
    #items_path = session["items_path"]
    order_path = session["order_path"]
    orders_pd = pd.read_csv(order_path)
    orders_d = orders_pd.to_dict(orient='records')
    return render_template("order_view.html", orders = orders_d)

@app.route('/order_view', methods = ["POST"])
def delete_order():
    #customer_upload_path = session["customer_upload_path"]
    #items_path = session["items_path"]
    order_path = session["order_path"]
    requests = {}
    for key, value in request.form.items():
        requests[key] = value

    if 'selected_name' in requests.keys():
        name = requests['selected_name']
        date = requests['selected_date']
        with open(order_path, 'r', newline='', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            rows = list(reader)

        condition = lambda row: (row['name'] == name) and (row['date'] == date)
        rows_to_keep = [row for row in rows if not condition(row)]

        with open(order_path, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows_to_keep)
    else:
        user_name = getpass.getuser()
        submit = requests['submit']
        destination_directory =f'C:\\Users\\{user_name}\\Downloads'

        # 현재 날짜를 YYYYMMDD 형식으로 얻기
        current_date = datetime.datetime.now().strftime("%Y%m%d")

        # 새 파일명 생성
        new_file_name = f"{current_date}_order.csv"
        new_file_path = os.path.join(destination_directory, new_file_name)

        # 파일 복사
        shutil.copyfile(order_path, new_file_path)
        flash('정상적으로 다운로드 되었습니다')

    return redirect(url_for('order_view'))

@app.route('/customer')
def view_customer():
    customer_upload_path = session["customer_upload_path"]
    #items_path = session["items_path"]
    order_path = session["order_path"]
    customers_pd = pd.read_csv(customer_upload_path)
    customers_d = customers_pd.to_dict(orient='records')
    return render_template('customer.html', customers = customers_d)

@app.route('/customer', methods = ["POST"])
def delete_customer():
    customer_path = session["customer_upload_path"]
    requests = {}
    for key, value in request.form.items():
        requests[key] = value

    with open(customer_path, 'r', newline='', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    if 'selected_name' in requests.keys():
        condition = lambda row: row['\ufeffname'] == requests['selected_name'] and row['ph'] == requests['selected_ph']
        rows_to_keep = [row for row in rows if not condition(row)]

        with open(customer_path, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows_to_keep)
    else:
        with open(customer_path, 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerows([[requests['new_name'], requests['new_phone'], requests['new_address']]])

    return redirect(url_for('view_customer'))

@app.route('/mypage')
def mypage():
    items_path = session["items_path"]
    items_pd = pd.read_csv(items_path)
    items_d = items_pd.to_dict(orient='records')
    return render_template('mypage.html', items = items_d)

@app.route('/mypage', methods = ['POST'])
def edit_mypage():
    items_path = session["items_path"]
    requests = {}
    for key, value in request.form.items():
        requests[key] = value
    print(requests.keys())
    with open(items_path, 'r', newline='', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    if 'delete_item' in requests.keys():
        condition = lambda row: row['\ufeffitem'] == requests['delete_item']
        rows_to_keep = [row for row in rows if not condition(row)]
        print(rows_to_keep)
        with open(items_path, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows_to_keep)


    if 'add_item' in requests.keys():
        with open(items_path, 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerows([[requests['add_item'], requests['add_price']]])

    return redirect(url_for('mypage'))


with app.test_request_context():
    #앱 내 파일 url 위치 불러오기
    print(url_for("hello-endpoint",ex="ex"))

@app.route('/upload', methods=['POST'])
def upload_file():
    customer_upload_path = session["customer_upload_path"]

    if 'file' not in request.files:
        return 'No file part'

    file = request.files['file']

    if file.filename == '':
        flash('파일이 없습니다')
        return redirect(url_for('mypage'))

    if file and file.filename.endswith('.csv'):
        #filename = f'customer_upload_}.csv'
        file.save(customer_upload_path)
        customers_upload_pd = pd.read_csv(customer_upload_path)
        expected_columns = ["name", "ph", "address"]
        if not all(column in customers_upload_pd.columns for column in expected_columns):
            flash("알맞지 않은 형식의 고객 파일입니다")
            return redirect(url_for("mypage"))
        flash('정상적으로 업로드 되었습니다')
        return redirect(url_for('mypage'))

    return 'Invalid file type'

if __name__ == '__main__':
    app.run(debug=True)