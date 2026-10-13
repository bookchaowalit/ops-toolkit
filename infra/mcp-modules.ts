/**
 * Loader for the mcp-aggregator modules the owner CLI shares (task state/store, habits, cells, CRM ledger,
 * portfolio reads). They live in the mcp-aggregator repository (SOLO_MCP_ROOT, default ../platform-mcp-aggregator), so the
 * CLI and the MCP server (and its tests) load the very same module instances.
 */
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** ops-toolkit checkout root (this file lives in <root>/infra). */
export const OPS_CHECKOUT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export const MCP_ROOT = process.env.SOLO_MCP_ROOT
  ? resolve(process.env.SOLO_MCP_ROOT)
  : resolve(OPS_CHECKOUT, '..', 'platform-mcp-aggregator');

/** Import `<mcp-aggregator>/src/<name>.ts`; exits with a clear message when the checkout is absent. */
export async function mcp(name: string): Promise<any> {
  const file = join(MCP_ROOT, 'src', `${name}.ts`);
  if (!existsSync(file)) {
    console.error(`mcp-aggregator checkout not found at ${MCP_ROOT} (set SOLO_MCP_ROOT); cannot load ${name}`);
    process.exit(2);
  }
  return import(pathToFileURL(file).href);
}
