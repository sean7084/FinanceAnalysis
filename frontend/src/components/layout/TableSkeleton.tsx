import { useI18n } from '../../useI18n'

interface TableSkeletonProps {
  columns: number
  rows?: number
  /** Rendered under the placeholder rows once newer data is on its way. */
  note?: string
}

/**
 * Placeholder rows matching the shape of the table they stand in for, so the
 * page height does not jump when real data lands.
 */
export function TableSkeleton({ columns, rows = 6, note }: TableSkeletonProps) {
  const { t } = useI18n()

  return (
    <div className="table-skeleton" role="status" aria-label={t('common.loading')}>
      {Array.from({ length: rows }, (_, rowIndex) => (
        <div key={rowIndex} className="table-skeleton-row">
          {Array.from({ length: columns }, (_, columnIndex) => (
            <span key={columnIndex} className="table-skeleton-cell" />
          ))}
        </div>
      ))}
      {note && <p className="table-skeleton-note">{note}</p>}
    </div>
  )
}
