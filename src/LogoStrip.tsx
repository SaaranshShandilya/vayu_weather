import type { CSSProperties } from "react";

type Logo = {
  src: string;
  alt: string;
  /**
   * Rendered height in rem. Heights are deliberately not uniform: a wide
   * wordmark set to the same height as a square emblem covers far more area
   * and reads as the bigger logo. These were solved for by measuring the
   * inked area each logo actually paints (aspect ratio x ink coverage) and
   * equalising it, subject to a floor that keeps the smallest lettering
   * legible. The result sits within 0.74x-1.52x of the mean, against
   * 0.24x-1.56x for a naive uniform height. Re-solve after replacing a file.
   */
  height: string;
};

const LOGOS: Logo[] = [
  {
    src: "/logos/blue-skies.png",
    alt: "International Day of Clean Air for blue skies",
    height: "2.8rem",
  },
  {
    src: "/logos/ccac.png",
    alt: "Climate & Clean Air Coalition",
    height: "3.3rem",
  },
  { src: "/logos/ceew.png", alt: "CEEW", height: "4rem" },
  {
    src: "/logos/coefficient-giving.png",
    alt: "Coefficient Giving",
    height: "4rem",
  },
  {
    src: "/logos/global-methane-hub.png",
    alt: "Global Methane Hub",
    height: "4.4rem",
  },
  {
    src: "/logos/iit-gandhinagar.png",
    alt: "IIT Gandhinagar",
    height: "4.8rem",
  },
];

export function LogoStrip() {
  return (
    <ul className="logo-strip" aria-label="Organisations">
      {LOGOS.map((logo) => (
        <li className="logo-strip__item" key={logo.src}>
          <img
            src={logo.src}
            alt={logo.alt}
            loading="lazy"
            decoding="async"
            style={{ "--logo-height": logo.height } as CSSProperties}
          />
        </li>
      ))}
    </ul>
  );
}
