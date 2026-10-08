async def test_audit_month_to_date_spend_uses_ceiling_when_actual_missing() -> None:
    from decimal import Decimal

    from pitwall.audit.capability import read_month_to_date_spend_usd
    from pitwall.cost.budget_gate import MONTH_TO_DATE_SPEND_SQL

    class _Conn:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def fetchrow(self, query: str, *args: object) -> dict[str, Decimal]:
            self.queries.append(query)
            return {"s": Decimal("12.5")}

    class _Acquire:
        def __init__(self, conn: _Conn) -> None:
            self._conn = conn

        async def __aenter__(self) -> _Conn:
            return self._conn

        async def __aexit__(self, *exc: object) -> None:
            return None

    class _Pool:
        def __init__(self) -> None:
            self.conn = _Conn()

        def acquire(self) -> _Acquire:
            return _Acquire(self.conn)

    pool = _Pool()
    spend = await read_month_to_date_spend_usd(pool)

    assert spend == Decimal("12.5")
    assert pool.conn.queries == [MONTH_TO_DATE_SPEND_SQL]
