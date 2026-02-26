import math

def optimize_pack_selection(required_qty, available_packs):
    if required_qty <= 0:
        return 0.0, [], 0

    scale = 1000
    req = int(math.ceil(required_qty * scale))
    scaled_packs = []
    # Filter and extract
    for i, p in enumerate(available_packs):
        size = int(round(p['size'] * scale))
        if size > 0:
            scaled_packs.append({'size': size, 'price': p['price'], 'orig': p, 'idx': i})

    if not scaled_packs:
        return float('inf'), [], 0

    max_size = max(p['size'] for p in scaled_packs)
    max_dp = req + max_size + 1

    dp = [float('inf')] * max_dp
    dp[0] = 0.0
    choice = [None] * max_dp

    for i in range(max_dp):
        if dp[i] == float('inf'):
            continue
        for p in scaled_packs:
            nxt = i + p['size']
            if nxt < max_dp:
                if dp[i] + p['price'] < dp[nxt]:
                    dp[nxt] = dp[i] + p['price']
                    choice[nxt] = (i, p)

    best_cost = float('inf')
    best_qty = req
    for j in range(req, max_dp):
        if dp[j] < best_cost:
            best_cost = dp[j]
            best_qty = j

    chosen = []
    curr = best_qty
    while curr > 0 and choice[curr] is not None:
        prev, p = choice[curr]
        chosen.append(p['orig'])
        curr = prev

    total_qty = sum(p['size'] for p in chosen)
    return best_cost, chosen, total_qty

packs = [
    {'size': 6, 'price': 12.0},
    {'size': 10, 'price': 18.0},
    {'size': 12, 'price': 22.0}
]

cost, chosen, qty = optimize_pack_selection(13, packs)
print(f"Cost: {cost}, Qty: {qty}")
for c in chosen:
    print(c)
