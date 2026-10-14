import { WindIcon } from "./icons";

export function BrandLogo({ className = "" }: { className?: string }) {
  return (
    <span className={`brand-mark ${className}`.trim()} aria-hidden="true">
      <WindIcon className="brand-mark__fallback" />
      <img src="/vayuchat-logo.png" alt="" width="512" height="512" />
    </span>
  );
}
