import os
import sys
sys.path.insert(0, os.getcwd())
from tests.test_data_writeback import WritebackTest

case = WritebackTest()
case.setUp()
try:
    mgr = case.load_manager()
    pi = mgr.data['pokemon_items']
    print('header_rel:', pi._header_rel)
    print('items count:', len(pi.data))
finally:
    case.tearDown()
