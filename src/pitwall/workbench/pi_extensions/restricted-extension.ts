import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { registerRestrictedBashTool } from './restricted.js';

export default function restrictedExtension(pi: ExtensionAPI): void {
  registerRestrictedBashTool(pi);
}
