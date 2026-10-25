import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import * as financeApi from "../../api/saasFinance";
import type { FinanceInvoiceFilters, FinanceInvoiceRow } from "../../api/saasFinance";
import * as tenantsApi from "../../api/tenants";

/** Shared between InvoicesPage.tsx and PaymentsPage.tsx - both are different views over the
 *  exact same rows, and sharing one fetch means they can never silently drift apart. */
export function useFinanceInvoices(filters: FinanceInvoiceFilters) {
  const [rows, setRows] = useState<FinanceInvoiceRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [markingId, setMarkingId] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    financeApi
      .listFinanceInvoices(filters)
      .then(setRows)
      .catch((err) => {
        setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load invoices." : "Could not load invoices.");
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(filters)]);

  useEffect(() => {
    load();
  }, [load]);

  async function markPaid(invoiceId: string, tenantId: string) {
    setMarkingId(invoiceId);
    try {
      await tenantsApi.markInvoicePaid(tenantId, invoiceId, true);
      load();
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not mark paid." : "Could not mark paid.");
    } finally {
      setMarkingId(null);
    }
  }

  return { rows, loading, error, markingId, markPaid, reload: load };
}
