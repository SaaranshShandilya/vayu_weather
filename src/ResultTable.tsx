import { TableIcon } from "./icons";
import type { ResultData } from "./types";

function formatCell(value: string | number | null) {
  if (value === null) return "—";
  if (typeof value === "number") {
    return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
  }
  return value;
}

export function ResultTable({ result }: { result: ResultData }) {
  if (!result.rows.length || !result.columns.length) return null;

  return (
    <details className="data-details">
      <summary>
        <TableIcon />
        View data
        <span>
          {result.rows.length}
          {result.truncated ? "+" : ""} rows
        </span>
      </summary>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {result.columns.map((column) => (
                <th key={column} scope="col">
                  {column.replaceAll("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, index) => (
              <tr key={index}>
                {result.columns.map((column) => (
                  <td key={column}>{formatCell(row[column] ?? null)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
