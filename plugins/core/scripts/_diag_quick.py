import sys; sys.path.insert(0, 'scripts')
from grandia_pnl import load_env, pg, shopify_credentials, shopify_mint_token, GRANDIA_SHOPIFY_DOMAIN
import requests, time
from collections import Counter
load_env()
met = pg('DATABASE_URL_METRICS')
creds = shopify_credentials(met, GRANDIA_SHOPIFY_DOMAIN)
access = shopify_mint_token(creds)
URL = f"https://{creds['shopifyDomain']}/admin/api/{creds['shopifyApiVersion']}/graphql.json"
H = {'X-Shopify-Access-Token': access, 'Content-Type': 'application/json'}
Q = """query($c:String,$q:String!){orders(first:250,after:$c,query:$q,sortKey:CREATED_AT){pageInfo{hasNextPage endCursor} edges{node{id createdAt displayFinancialStatus cancelledAt currentTotalPriceSet{shopMoney{amount}}}}}}"""
def run(q):
    cur=None; rows=[]
    while True:
        r=requests.post(URL,headers=H,json={'query':Q,'variables':{'c':cur,'q':q}},timeout=60).json()
        d=r['data']['orders']
        rows+=[e['node'] for e in d['edges']]
        if not d['pageInfo']['hasNextPage']: break
        cur=d['pageInfo']['endCursor']; time.sleep(0.2)
    return rows
for q,label in [
    ('created_at:>=2026-05-02 created_at:<=2026-05-31T23:59:59+03:00','May 2-31 strict'),
    ('created_at:>=2026-05-01 created_at:<=2026-05-31T23:59:59+03:00','May 1-31 strict'),
    ('created_at:>=2026-04-01 created_at:<=2026-04-30T23:59:59+03:00','Apr 1-30 strict'),
]:
    rows=run(q)
    cs=Counter(r['displayFinancialStatus'] for r in rows)
    tot=sum(float(r['currentTotalPriceSet']['shopMoney']['amount']) for r in rows)
    canc=sum(1 for r in rows if r.get('cancelledAt'))
    if rows:
        first=min(r['createdAt'] for r in rows); last=max(r['createdAt'] for r in rows)
    else:
        first=last='-'
    print(f'{label}: orders={len(rows)} total={tot:,.2f} cancelled={canc} first={first} last={last}')
    print(f'  status={dict(cs)}')
