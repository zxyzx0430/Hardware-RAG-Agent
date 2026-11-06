/** Whether the chat history paging bar has anything meaningful to show. */

export interface HistoryPaginationStatus {
  loading: boolean;
  pagesLoaded: number;
  hasMore: boolean | null;
  error: string | null;
  persistenceError: string | null;
}

/**
 * A brand-new 0-message session has no history to page. Showing "加载历史分页"
 * there is a dead control, so the bar only appears once a page was requested,
 * history exists, or the page request itself has a state to report.
 */
export function shouldShowHistoryPagination(
  messageCount: number,
  status: HistoryPaginationStatus | null | undefined,
): boolean {
  if (messageCount > 0) return true;
  if (!status) return false;
  return status.pagesLoaded > 0
    || status.loading
    || Boolean(status.error)
    || Boolean(status.persistenceError);
}
