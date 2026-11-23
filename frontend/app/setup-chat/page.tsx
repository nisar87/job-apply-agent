import { redirect } from "next/navigation";

// The old question-and-answer page is gone: describing a search happens in the one box.
export default function SetupChat() {
  redirect("/setup");
}
