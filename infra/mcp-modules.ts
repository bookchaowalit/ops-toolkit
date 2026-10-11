/**
 * Loader for the solo-mcp modules the owner CLI shares (task state/store, habits, cells, CRM ledger,
 * portfolio reads). They live in the solo-mcp repository (SOLO_MCP_ROOT, default ../solo-mcp), so the
 * CLI and the MCP server (and its tests) load the very same module instances.
 */
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** solo-ops checkout root (this file lives in <root>/infra). */
export const OPS_CHECKOUT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export const MCP_ROOT = process.env.SOLO_MCP_ROOT
  ? resolve(process.env.SOLO_MCP_ROOT)
  : resolve(OPS_CHECKOUT, '..', 'solo-mcp');

/** Import `<solo-mcp>/src/<name>.ts`; exits with a clear message when the checkout is absent. */
export async function mcp(name: string): Promise<any> {
  const file = join(MCP_ROOT, 'src', `${name}.ts`);
  if (!existsSync(file)) {
    console.error(`solo-mcp checkout not found at ${MCP_ROOT} (set SOLO_MCP_ROOT); cannot load ${name}`);
    process.exit(2);
  }
  return import(pathToFileURL(file).href);
}
