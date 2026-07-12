import { useState } from "react";

interface InlineInputProps {
  initialValue: string;
  placeholder?: string;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}

export function InlineInput({ initialValue, placeholder, onSubmit, onCancel }: InlineInputProps) {
  const [value, setValue] = useState(initialValue);

  const submit = () => onSubmit(value);
  const cancel = () => onCancel();

  return (
    <input
      className="explorer-tree-rename-input"
      type="text"
      value={value}
      placeholder={placeholder}
      autoFocus
      onChange={(e) => setValue(e.target.value)}
      onBlur={submit}
      onKeyDown={(e) => {
        if (e.key === "Enter") submit();
        if (e.key === "Escape") cancel();
      }}
    />
  );
}
