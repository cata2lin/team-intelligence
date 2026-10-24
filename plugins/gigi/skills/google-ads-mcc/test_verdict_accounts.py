#!/usr/bin/env python3
# Test pur pt brandref.verdict_accounts() — fara KB (fixture). Ruleaza: uv run test_verdict_accounts.py
import importlib.util, os
HERE=os.path.dirname(os.path.abspath(__file__))
spec=importlib.util.spec_from_file_location("brandref",os.path.join(HERE,"brandref.py"))
br=importlib.util.module_from_spec(spec); spec.loader.exec_module(br)

def test_verdict_accounts():
    d={
      "grandia":{"gads_verdict":True,"gads_cid":"9069610821","gads_currency":"RON","breakeven_cpa":74},
      "casaofertelor":{"gads_verdict":True,"gads_cid":"1438021539","gads_currency":"RON","breakeven_cpa":40},
      "bonhaus_cz":{"gads_verdict":True,"gads_cid":"3141935298","gads_currency":"CZK","breakeven_cpa":37},
      "esteban":{"gads_verdict":True,"gads_cid":"8467882152","gads_cid_vechi":"5229815058","breakeven_cpa":62},
      "belasil":{"gads_cid":"7566352958","breakeven_cpa":40},   # fara gads_verdict -> EXCLUS (vandut)
      "apreciat":{"breakeven_cpa":49},                          # fara flag -> EXCLUS
      "labnoir":{"gads_verdict":True,"gads_cid":"x"},           # fara breakeven -> EXCLUS
      "_note":"string",                                         # non-dict -> skip
    }
    acc=br.verdict_accounts(d); brands=[b for b,_,_ in acc]
    assert "grandia" in brands and "casaofertelor" in brands and "esteban" in brands and "bonhaus_cz" in brands
    assert "belasil" not in brands and "apreciat" not in brands and "labnoir" not in brands
    assert ("bonhaus_cz","3141935298","CZK") in acc
    assert ("esteban","8467882152","RON") in acc
    assert acc==sorted(acc)
    print("OK verdict_accounts:",brands)

if __name__=="__main__":
    test_verdict_accounts()
