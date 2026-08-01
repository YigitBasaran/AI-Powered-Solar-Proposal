"use client";

import { useEffect, useState } from "react";

import { DIALLING_CODES, joinPhone, splitPhone } from "@/lib/phone";
import { Input, cn } from "@/components/ui/primitives";

/**
 * A phone field with its dialling code beside it.
 *
 * The stored form is always international — `+27 21 555 0100` — because a
 * proposal is sold across borders and a bare local number is only dialable by
 * someone who already knows which country it belongs to.
 *
 * **The code is local state, and it has to be.** An empty phone stores an
 * empty string, not `"+90"`, because a customer with no phone number has no
 * phone number. So a purely derived code resets to the default the moment the
 * number is blank — which means picking the country *before* typing the digits
 * silently discarded the choice, and the number was then saved under the wrong
 * country. Keeping the selection here lets it survive until there is a number
 * to attach it to.
 */
export function PhoneInput({
  id,
  value,
  onChange,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const [code, setCode] = useState(() => splitPhone(value).code);

  // A value arriving from outside — loading a customer for editing — carries
  // its own code, and that wins over whatever was selected for a blank field.
  useEffect(() => {
    if (value.trim()) setCode(splitPhone(value).code);
  }, [value]);

  const rest = value.startsWith(code) ? value.slice(code.length).trim() : splitPhone(value).rest;

  return (
    <div className="flex gap-2">
      <label htmlFor={`${id}-code`} className="sr-only">
        Country dialling code
      </label>
      <select
        id={`${id}-code`}
        value={code}
        disabled={disabled}
        data-testid={`${id}-code`}
        onChange={(event) => {
          setCode(event.target.value);
          onChange(joinPhone(event.target.value, rest));
        }}
        className={cn(
          "shrink-0 rounded-lg border border-slate-line bg-surface px-2 py-1.5",
          "text-[13px] text-slate-ink disabled:bg-surface-2",
          "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-navy-700",
        )}
      >
        {DIALLING_CODES.map((entry) => (
          <option key={entry.iso} value={entry.code}>
            {entry.code} {entry.country}
          </option>
        ))}
      </select>

      <Input
        id={id}
        type="tel"
        value={rest}
        disabled={disabled}
        placeholder="21 555 0100"
        onChange={(next) => onChange(joinPhone(code, next))}
      />
    </div>
  );
}
