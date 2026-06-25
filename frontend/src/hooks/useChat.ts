import { useChatStore } from "../stores/useChatStore";

export function useChat() {
  const sendMessage = useChatStore((s) => s.sendMessage);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const messages = useChatStore((s) => s.messages);
  const stopStreaming = useChatStore((s) => s.stopStreaming);

  return {
    sendMessage,
    isStreaming,
    messages,
    stopStreaming,
  };
}
