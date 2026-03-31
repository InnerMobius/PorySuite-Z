import os
import sys
import json
sys.path.insert(0, os.getcwd())
from tests.test_data_writeback import WritebackTest

case = WritebackTest()
case.setUp()
try:
    mgr = case.load_manager()
    pi = mgr.data['pokemon_items']
    print('header_rel:', pi._header_rel)
    pi.data['ITEM_TEST']['name'] = '_("CHANGED")'
    print('before save entry:', pi.data['ITEM_TEST'])
    mgr.parse_to_c_code()
    print('after save entry:', pi.data['ITEM_TEST'])
    header_path = os.path.join(case.project_info['dir'], 'src', 'data', 'graphics', 'items.h')
    with open(header_path, encoding='utf-8') as f:
        header_content = f.read()
    print('header:', header_content)
    json_path = os.path.join(case.project_info['dir'], 'src', 'data', 'items.json')
    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)
    print('json entry:', data['ITEM_TEST'])
finally:
    case.tearDown()
