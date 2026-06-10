import React, { useState } from "react";

interface BubbleTextProps {
  text: string;
  className?: string;
}

export const BubbleText = ({ text, className = "" }: BubbleTextProps) => {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  return (
    <span
      onMouseLeave={() => setHoveredIndex(null)}
      className={className}
    >
      {text.split("").map((char, idx) => {
        const distance = hoveredIndex !== null ? Math.abs(hoveredIndex - idx) : null;
        
        let fontClass = "font-bold"; // default weight from the h1
        
        if (distance === 0) {
          fontClass = "font-black text-white mix-blend-normal"; // pop to pure white
        } else if (distance === 1) {
          fontClass = "font-extrabold";
        } else {
          fontClass = "font-bold";
        }

        return (
          <span
            key={idx}
            onMouseEnter={() => setHoveredIndex(idx)}
            className={`transition-all duration-300 ease-in-out cursor-default inline-block ${fontClass}`}
          >
            {char === " " ? "\u00A0" : char}
          </span>
        );
      })}
    </span>
  );
};
