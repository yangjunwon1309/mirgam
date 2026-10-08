"""Import the seasonal order workbook into the CSV layout used by Mirgam.

The source workbook is read only.  Run this script after replacing
``static/order_new.xlsx`` with a newer export.
"""

from pathlib import Path
import shutil

import pandas as pd


STATIC_DIR = Path(__file__).resolve().parent / "static"
SOURCE_PATH = STATIC_DIR / "order_new.xlsx"
FARM_NAME = "준이네 농장"

SOURCE_COLUMNS = [
    "주문일",
    "주문자",
    "주문자 PH",
    "주문자 주소",
    "수량",
    "보내는 사람",
    "보내는 사람 PH",
    "보내는 사람 주소",
    "상품종류",
]

ORDER_COLUMNS = [
    "name",
    "ph",
    "address",
    "item",
    "quantity",
    "date",
    "sender_name",
    "sender_ph",
    "sender_address",
]
CUSTOMER_COLUMNS = ["name", "ph", "address"]


def clean_text(value):
    """Keep empty cells empty and remove only accidental outer whitespace."""
    if pd.isna(value):
        return ""
    return str(value).strip()


def backup_if_present(path: Path) -> None:
    if path.exists():
        backup_dir = STATIC_DIR / "archive_before_order_new_import"
        backup_dir.mkdir(exist_ok=True)
        shutil.copy2(path, backup_dir / path.name)


def build_datasets(source: pd.DataFrame):
    """Build Mirgam's three CSV datasets without writing any files."""
    if list(source.columns) != SOURCE_COLUMNS:
        raise ValueError(
            "Unexpected source columns. Expected: "
            f"{SOURCE_COLUMNS}; received: {list(source.columns)}"
        )

    # Preserve the full source record in the order file.  The first six
    # columns remain compatible with the existing Mirgam views.
    order = pd.DataFrame(
        {
            "name": source["주문자"].map(clean_text),
            "ph": source["주문자 PH"].map(clean_text),
            "address": source["주문자 주소"].map(clean_text),
            "item": source["상품종류"].map(clean_text),
            "quantity": source["수량"].map(clean_text),
            "date": pd.to_datetime(source["주문일"], errors="coerce").dt.strftime("%Y-%m-%d").fillna(""),
            "sender_name": source["보내는 사람"].map(clean_text),
            "sender_ph": source["보내는 사람 PH"].map(clean_text),
            "sender_address": source["보내는 사람 주소"].map(clean_text),
        },
        columns=ORDER_COLUMNS,
    )

    # Customers are delivery recipients (the workbook's 주문자 fields).
    # Keep distinct name/phone/address combinations; do not merge records
    # merely because a phone number is shared by a household or organisation.
    customers = order[CUSTOMER_COLUMNS].copy()
    customers = customers[customers["name"] != ""]
    customers = customers.drop_duplicates(ignore_index=True)

    # The workbook contains product names but no trustworthy price data.
    # Add every product so it can be selected in the app; price stays blank.
    items = pd.DataFrame(
        {"item": [item for item in order["item"].drop_duplicates() if item], "price": ""}
    )

    return order, customers, items


def main() -> None:
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"Source workbook not found: {SOURCE_PATH}")

    source = pd.read_excel(SOURCE_PATH, sheet_name=0)
    order, customers, items = build_datasets(source)

    targets = [
        STATIC_DIR / f"order_{FARM_NAME}.csv",
        STATIC_DIR / f"customer_{FARM_NAME}.csv",
        STATIC_DIR / f"customer_upload_{FARM_NAME}.csv",
        STATIC_DIR / "items.csv",
    ]
    for target in targets:
        backup_if_present(target)

    order.to_csv(targets[0], index=False, encoding="utf-8-sig", lineterminator="\n")
    customers.to_csv(targets[1], index=False, encoding="utf-8-sig", lineterminator="\n")
    customers.to_csv(targets[2], index=False, encoding="utf-8-sig", lineterminator="\n")
    items.to_csv(targets[3], index=False, encoding="utf-8-sig", lineterminator="\n")

    print(f"Source preserved: {SOURCE_PATH}")
    print(f"Orders written: {len(order)} -> {targets[0].name}")
    print(f"Customers written: {len(customers)} -> {targets[2].name}")
    print(f"Products written: {len(items)} -> {targets[3].name}")


if __name__ == "__main__":
    main()
