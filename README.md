# Mirgam

Mirgam is a lightweight CRM web application for farmers who sell agricultural products directly to customers.

It helps farm operators manage customer information, register orders, maintain product lists, and export order data without using a separate database.

## Core Features

### Customer Management

- View registered customers
- Add new customers
- Delete existing customers
- Import customer data from CSV files
- Store customer names, phone numbers, and addresses

### Order Management

- Search and select customers
- Register the same order for multiple customers
- Select a product and enter the order quantity
- Automatically record the order date and time
- View and delete existing orders
- Export order history as a CSV file

### Product Management

- View available products
- Add products with prices
- Delete products
- Use registered products in the order form

### Farm Login

- Authenticate farm accounts using a farm name and password
- Maintain separate customer and order files for each farm account

## Core Architecture

Mirgam is built as a simple Flask application using server-rendered HTML templates.

```text
Browser
   ↓
Flask Routes
   ↓
Business Logic in app.py
   ↓
CSV Data Files
```

### Backend

- Python
- Flask
- Pandas
- Python CSV module
- Flask sessions

The main application logic is implemented in:

```text
apps/app.py
```

This file handles:

- User authentication
- Page routing
- Customer CRUD operations
- Order CRUD operations
- Product management
- CSV uploads and exports
- Farm-specific file paths

### Frontend

The frontend uses:

- HTML
- CSS
- JavaScript
- Jinja2 templates

Page templates are stored in:

```text
apps/templates/
```

Main pages include:

- `login.html` — farm login
- `order.html` — order registration
- `order_view.html` — order history
- `customer.html` — customer management
- `mypage.html` — product management and CSV upload

### Data Storage

The application does not use a database. Data is stored in CSV files under:

```text
apps/static/
```

Main data files include:

```text
login.csv
items.csv
customer_upload_<farm_name>.csv
order_<farm_name>.csv
```

Customer and order files are separated by farm account, while the product list is shared across the application.

## Project Structure

```text
mirgam/
├── apps/
│   ├── app.py
│   ├── static/
│   │   ├── login.csv
│   │   ├── items.csv
│   │   ├── customer_upload_<farm_name>.csv
│   │   └── order_<farm_name>.csv
│   └── templates/
│       ├── login.html
│       ├── order.html
│       ├── order_view.html
│       ├── customer.html
│       └── mypage.html
├── requirements.txt
└── .env
```

## Application Flow

```text
Farm Login
    ↓
Register or Import Customers
    ↓
Add Products
    ↓
Create Orders
    ↓
Review, Delete, or Export Orders
```

## Running the Application

```bash
git clone https://github.com/yangjunwon1309/mirgam.git
cd mirgam

pip install -r requirements.txt
python apps/app.py
```

Open the application in a browser:

```text
http://127.0.0.1:5000
```

## Notes

Mirgam is designed as a small-scale prototype.

Because it uses CSV files instead of a database, it is best suited for local use, demonstrations, or small farm operations. For production use, database integration, password hashing, CSRF protection, and improved authentication should be added.
