def test_timing_cxp(client, auth_cookies):
    import time
    t0 = time.time()
    r = client.get("/finanzas/cuentas-por-pagar", cookies=auth_cookies)
    dt = time.time() - t0
    print(f"\nCXP status={r.status_code} tiempo={dt:.2f}s")
    assert r.status_code == 200
