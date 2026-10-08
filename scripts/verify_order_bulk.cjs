// Exercise the order page's real script without touching account CSVs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
function makeElement() {
    return {
        value: '', textContent: '', dataset: {}, children: [], handlers: {}, attributes: {},
        get innerHTML() { return this.html || this.textContent.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); },
        set innerHTML(value) { this.html = value; this.children = []; },
        classList: { toggle() {} },
        addEventListener(name, callback) { this.handlers[name] = callback; },
        appendChild(child) { this.children.push(child); },
        setAttribute(name, value) { this.attributes[name] = value; },
        checkValidity() { return this.valid !== false; }, reportValidity() { return this.valid !== false; },
        focus() {}, reset() {},
        showModal() { this.open = true; }, close() { this.open = false; },
    };
}
function element(id) {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
}
const bulkFields = ['item', 'quantity', 'date', 'sender_name', 'sender_ph', 'sender_address']
    .map(field => Object.assign(element(`bulk-${field}`), { dataset: { bulkField: field } }));
const document = {
    getElementById: element,
    createElement: makeElement,
    querySelectorAll: () => bulkFields,
};
const template = fs.readFileSync(path.join(__dirname, '../apps/templates/order.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace(/const ITEMS=.*?;/, 'const ITEMS=["상품 A","상품 B"],TODAY="2026-10-06";')
    .replace(/const DRAFT_ORDERS=.*?;/, 'const DRAFT_ORDERS=[];');
const context = vm.createContext({ document, setTimeout, clearTimeout });
vm.runInContext(script, context);
const run = code => vm.runInContext(code, context);
run('applyAllOrders()');
assert.match(element('bulk-status').textContent, /먼저/);
run('toggleCustomer({customer_id:1,name:"고객 A",phone:"010-1",address:"주소 A"}); toggleCustomer({customer_id:2,name:"고객 B",phone:"010-2",address:"주소 B"})');
run('selectedList()[0].quantity="2"; selectedList()[1].quantity="5"; selectedList()[0].date="2025-12-01"; applyAllOrders()');
assert.match(element('bulk-status').textContent, /항목/);
bulkFields[0].value = '상품 B';
bulkFields[3].value = '홍길동';
bulkFields[4].value = '010-1234-5678';
bulkFields[5].value = '발송 주소';
run('applyAllOrders()');
let orders = JSON.parse(element('orders').value);
assert.deepEqual(orders.map(order => order.quantity), ['2', '5']);
assert.equal(orders[0].date, '2025-12-01');
assert.ok(orders.every(order => order.item === '상품 B' && order.sender_name === '홍길동'
    && order.sender_ph === '010-1234-5678' && order.sender_address === '발송 주소'));
bulkFields[1].value = '3';
bulkFields[2].value = '2026-10-10';
run('applyAllOrders()');
orders = JSON.parse(element('orders').value);
assert.ok(orders.every(order => order.quantity === '3' && order.date === '2026-10-10'));
run('updateOrderField({target:{dataset:{key:customerKey(selectedList()[0]),field:"quantity"},value:"7"}})');
assert.deepEqual(JSON.parse(element('orders').value).map(order => order.quantity), ['7', '3']);
run('toggleCustomer({customer_id:3,name:"고객 C",phone:"010-3",address:"주소 C"})');
assert.equal(JSON.parse(element('orders').value)[2].quantity, '');
const beforeInvalidApply = element('orders').value;
bulkFields[0].value = '상품 A';
bulkFields[1].value = '-1';
bulkFields[1].valid = false;
run('applyAllOrders()');
assert.equal(element('orders').value, beforeInvalidApply);
run('toggleCustomer({customer_id:4,name:"Same",phone:"010-4",address:"Address A"}); toggleCustomer({customer_id:5,name:"Same",phone:"010-4",address:"Address \\"B\\""})');
orders = JSON.parse(element('orders').value).filter(order => order.name === 'Same');
assert.equal(orders.length, 2);
run('updateOrderField({target:{dataset:{key:customerKey(selectedList().find(order=>order.address==="Address A")),field:"quantity"},value:"8"}})');
orders = JSON.parse(element('orders').value).filter(order => order.name === 'Same');
assert.deepEqual(orders.map(order => order.quantity), ['8', '']);
run('toggleCustomer({customer_id:4,name:"Same",phone:"010-4",address:"Address A"})');
orders = JSON.parse(element('orders').value).filter(order => order.name === 'Same');
assert.equal(orders.length, 1);
assert.equal(orders[0].address, 'Address "B"');
run('renderSearch([{id:6,name:"Search Customer",ph:"010-5",address:"Address A"},{id:7,name:"Search Customer",ph:"010-5",address:"Address B"},{id:6,name:"Search Customer",ph:"010-5",address:"Address A"}])');
let buttons = element('search-results').children;
assert.equal(buttons.length, 2);
assert.deepEqual(buttons.map(button => button.children[1].textContent), ['Address A', 'Address B']);
assert.equal(buttons[0].children[1].className, 'customer-address');
buttons[0].handlers.click();
buttons = element('search-results').children;
assert.deepEqual(buttons.map(button => button.attributes['aria-pressed']), ['true', 'false']);
assert.equal(JSON.parse(element('orders').value).filter(order => order.name === 'Search Customer').length, 1);
buttons[0].handlers.click();
assert.deepEqual(element('search-results').children.map(button => button.attributes['aria-pressed']), ['false', 'false']);
console.log('PASS: bulk fields, blank-field preservation, individual edits, later customers, and invalid input');
console.log('PASS: same name and phone with different addresses select, edit and deselect independently');
console.log('PASS: search cards show addresses, collapse exact duplicates, and highlight only the clicked customer');

(async () => {
    let pendingSearch;
    context.setTimeout = callback => { pendingSearch = callback; return 1; };
    context.clearTimeout = () => {};
    context.fetch = async () => ({ ok: true, json: async () => [] });
    const search = element('customer_name_input');
    search.value = 'New Customer';
    search.handlers.input.call(search);
    await pendingSearch();
    assert.match(element('customer-feedback').textContent, /검색 결과가 없습니다/);
    element('add-customer').handlers.click();
    assert.equal(element('new-customer-dialog').open, true);
    assert.equal(element('quick-new-name').value, 'New Customer');
    element('quick-new-phone').value = '010-6666';
    element('quick-new-address').value = 'New address';
    const previousOrders = JSON.parse(element('orders').value);
    context.fetch = async (url, options) => {
        assert.equal(url, '/customers/new');
        assert.deepEqual(JSON.parse(options.body), {new_name:'New Customer',new_phone:'010-6666',new_address:'New address'});
        return {ok:true,json:async()=>({created:true,customer:{id:9,name:'New Customer',ph:'010-6666',address:'New address'}})};
    };
    const submit = element('new-customer-form').handlers.submit;
    await submit({ preventDefault() {} });
    const afterRegistration = JSON.parse(element('orders').value);
    assert.deepEqual(afterRegistration.slice(0, previousOrders.length), previousOrders);
    assert.equal(afterRegistration.at(-1).name, 'New Customer');
    assert.equal(element('new-customer-dialog').open, false);
    assert.equal(element('search-results').children[0].attributes['aria-pressed'], 'true');
    const beforeCancel = element('orders').value;
    element('add-customer').handlers.click();
    element('cancel-customer-dialog').handlers.click();
    assert.equal(element('orders').value, beforeCancel);
    context.fetch = async () => ({ok:true,json:async()=>({created:false,customer:{id:9,name:'New Customer',ph:'010-6666',address:'New address'}})});
    await submit({preventDefault(){}});
    assert.equal(element('orders').value, beforeCancel);
    element('add-customer').handlers.click();
    context.fetch = async () => ({ok:false,json:async()=>({message:'주소를 입력해 주세요.'})});
    await submit({preventDefault(){}});
    assert.equal(element('new-customer-dialog').open, true);
    assert.equal(element('new-customer-error').textContent, '주소를 입력해 주세요.');
    assert.equal(element('orders').value, beforeCancel);
    let releaseOldSearch;
    context.fetch = async url => {
        if(url.includes('Old'))return await new Promise(resolve=>{releaseOldSearch=resolve});
        return {ok:true,json:async()=>[]};
    };
    search.value='Old query';search.handlers.input.call(search);const oldSearch=pendingSearch();
    search.value='Latest query';search.handlers.input.call(search);await pendingSearch();
    releaseOldSearch({ok:true,json:async()=>[{name:'Old customer',ph:'010-old',address:'Old address'}]});
    await oldSearch;
    assert.equal(element('search-results').children.length,0);
    assert.match(element('customer-feedback').textContent,/Latest query/);
    console.log('PASS: no-result prompt, popup name prefill, registration selection, draft preservation, cancel and errors');
    console.log('PASS: delayed search results cannot overwrite the latest customer search');
})().catch(error => { console.error(error); process.exitCode = 1; });
