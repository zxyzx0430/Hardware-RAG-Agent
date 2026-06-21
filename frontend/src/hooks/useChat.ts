import { useChatStore } from "../stores/useChatStore";

export function useChat() {
  const { sendMessage, isStreaming, messages, stopStreaming } = useChatStore();

  return {
    sendMessage,
    isStreaming,
    messages,
    stopStreaming,
  };
}
