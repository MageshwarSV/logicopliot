/** Small hand-written inline icons for StatCard's `icon` prop - no icon library is installed
 *  in this frontend, so these are plain 24x24 outline SVGs (stroke=currentColor, inherits the
 *  StatCard tone's own text color) rather than a dependency. */
import type { SVGProps } from "react";

function Icon({ children, ...rest }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-5 w-5"
      {...rest}
    >
      {children}
    </svg>
  );
}

export function DocumentIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M7 3h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M14 3v4h4" />
      <path d="M9 13h6M9 17h6" />
    </Icon>
  );
}

export function FolderIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M3 7a1 1 0 0 1 1-1h4.5l2 2H20a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z" />
    </Icon>
  );
}

export function TrashIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 7h16" />
      <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
      <path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13" />
      <path d="M10 11v6M14 11v6" />
    </Icon>
  );
}

export function LayersIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="m12 3 9 5-9 5-9-5 9-5Z" />
      <path d="m3 13 9 5 9-5" />
    </Icon>
  );
}

export function CheckCircleIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx={12} cy={12} r={9} />
      <path d="m8.5 12.5 2.5 2.5 4.5-5.5" />
    </Icon>
  );
}

export function ExclamationTriangleIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M12 4 2.5 20h19L12 4Z" />
      <path d="M12 10v4" />
      <circle cx={12} cy={17.2} r={0.4} fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function ArrowPathIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 10a8 8 0 0 1 14.3-4.9M20 4v5h-5" />
      <path d="M20 14a8 8 0 0 1-14.3 4.9M4 20v-5h5" />
    </Icon>
  );
}

export function SearchIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx={11} cy={11} r={7} />
      <path d="m20 20-3.5-3.5" />
    </Icon>
  );
}

export function DownloadIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M4 19h16" />
    </Icon>
  );
}

export function InfoIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon strokeWidth={2} {...props}>
      <circle cx={12} cy={12} r={9} />
      <path d="M12 11v5" />
      <circle cx={12} cy={8} r={0.5} fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function TargetIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx={12} cy={12} r={9} />
      <circle cx={12} cy={12} r={5} />
      <circle cx={12} cy={12} r={1} fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function UsersIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx={9} cy={8} r={3} />
      <path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
      <path d="M16 5.5a3 3 0 0 1 0 5.9" />
      <path d="M17 14c2.8.5 5 2.9 5 6" />
    </Icon>
  );
}

export function ClockIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx={12} cy={12} r={9} />
      <path d="M12 7v5l3.5 2" />
    </Icon>
  );
}

export function ArrowRightIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 12h16" />
      <path d="m13 6 6 6-6 6" />
    </Icon>
  );
}

export function CreditCardIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <rect x={3} y={5} width={18} height={14} rx={2} />
      <path d="M3 9.5h18" />
      <path d="M6.5 14h3" />
    </Icon>
  );
}

export function GearIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx={12} cy={12} r={3} />
      <path d="M19.4 13.5a7.6 7.6 0 0 0 0-3l1.9-1.5-2-3.4-2.3.8a7.5 7.5 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.4a7.5 7.5 0 0 0-2.6 1.5l-2.3-.8-2 3.4L4.6 10.5a7.6 7.6 0 0 0 0 3L2.7 15.5l2 3.4 2.3-.8a7.5 7.5 0 0 0 2.6 1.5l.4 2.4h4l.4-2.4a7.5 7.5 0 0 0 2.6-1.5l2.3.8 2-3.4Z" />
    </Icon>
  );
}

export function BriefcaseIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <rect x={3} y={8} width={18} height={12} rx={2} />
      <path d="M8 8V6a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M3 13h18" />
    </Icon>
  );
}

export function ChartBarIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 20V10" />
      <path d="M12 20V4" />
      <path d="M20 20v-7" />
    </Icon>
  );
}

export function DatabaseIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <ellipse cx={12} cy={6} rx={8} ry={3} />
      <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
      <path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
    </Icon>
  );
}

export function ChevronDownIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon strokeWidth={2.25} {...props}>
      <path d="m6 9 6 6 6-6" />
    </Icon>
  );
}

export function CalendarIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <rect x={3.5} y={5} width={17} height={16} rx={2} />
      <path d="M3.5 9.5h17" />
      <path d="M8 3v4M16 3v4" />
    </Icon>
  );
}
