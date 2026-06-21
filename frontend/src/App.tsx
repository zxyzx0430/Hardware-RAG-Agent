import { useTheme } from "./hooks/useTheme";
import { useKeyboard, useDefaultKeyboardShortcuts } from "./hooks/useKeyboard";
import { AppRoot } from "./components/layout/AppRoot";

export default function App() {
  useTheme();
  useKeyboard(useDefaultKeyboardShortcuts());

  return <AppRoot />;
}
