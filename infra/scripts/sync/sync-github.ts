/**
 * GitHub Sync Script
 * Fetches all repos from GitHub API and imports them as project_repos
 *
 * Usage:
 *   GITHUB_PERSONAL_ACCESS_TOKEN=xxx npx tsx infra/scripts/sync/sync-github.ts
 *   GITHUB_PERSONAL_ACCESS_TOKEN=xxx npx tsx infra/scripts/sync/sync-github.ts --full
 */

import { mcp } from "../../mcp-modules.js";
import https from "https";

const { openCellStore } = await mcp("cell-store");
const { sortRows } = await mcp("portfolio-reads");

const GITHUB_TOKEN = process.env.GITHUB_PERSONAL_ACCESS_TOKEN || process.env.GITHUB_TOKEN;
const GITHUB_USER = "bookchaowalit";
const PER_PAGE = 100;

if (!GITHUB_TOKEN) {
    console.error("Error: GITHUB_PERSONAL_ACCESS_TOKEN (or legacy GITHUB_TOKEN) is required");
    console.error("Usage: GITHUB_PERSONAL_ACCESS_TOKEN=xxx npx tsx infra/scripts/sync/sync-github.ts");
    process.exit(1);
}

// ─── Repo categorization logic ───────────────────────────────
interface RepoCategory {
    category: string;
    project?: string;
    role: string;
}

function categorizeRepo(name: string): RepoCategory {
    // Excluded repos (belong to another computer)
    const excluded = ['cute-crm', 'cute-crm-frontend', 'cute-crm-backend', 'adviser-plus-frontend', 'adviser-plus-backend', 'adviser-cms', 'adviser-script'];
    if (excluded.includes(name)) return { category: "internal", project: "excluded", role: "other" };

    // Portfolio frontends
    if (name.startsWith("bookchaowalit-") && name.endsWith("-frontend")) {
        return { category: "portfolio", project: "101-portfolio-projects", role: "frontend" };
    }

    // Core products
    if (name === "localcrm-frontend" || name === "localcrm-backend") return { category: "product", project: "localcrm", role: name.includes("frontend") ? "frontend" : "backend" };
    if (name === "booktrading") return { category: "product", project: "book-series", role: "fullstack" };
    if (name === "bookreading") return { category: "product", project: "book-series", role: "fullstack" };
    if (name === "bookmarketing") return { category: "product", project: "book-series", role: "fullstack" };

    // AI/ML
    if (name.includes("ai") || name.includes("llm") || name.includes("prompt") || name.includes("chatbot") || name.includes("sentiment") || name.includes("summarizer")) {
        return { category: "experiment", project: "ai-projects", role: "fullstack" };
    }

    // Client work
    const clientPatterns = ["table-grow"];
    for (const p of clientPatterns) {
        if (name.includes(p)) return { category: "client", project: "client-work", role: "fullstack" };
    }

    // Infrastructure
    if (name.includes("docker") || name.includes("backup")) return { category: "internal", project: "infrastructure", role: "infra" };

    // Docs
    if (name.includes("docs") || name.includes("mermaid") || name.includes("flowchart") || name.includes("git-edu") || name.includes("postman")) {
        return { category: "internal", project: "documentation", role: "docs" };
    }

    // Research
    if (name.includes("research") || name.includes("ocr") || name.includes("audio") || name.includes("novel")) {
        return { category: "experiment", project: "research", role: "fullstack" };
    }

    // Solo Empire
    if (name === "solo-empire") return { category: "internal", project: "solo-empire", role: "fullstack" };

    // Other known projects
    if (name === "landingpage") return { category: "product", project: "landing-page", role: "fullstack" };
    if (name === "media-tracker") return { category: "product", project: "media-tracker", role: "fullstack" };
    if (name === "go-gofiber-media-tracking") return { category: "product", project: "media-tracker", role: "backend" };

    return { category: "experiment", role: "fullstack" };
}

// ─── GitHub API helper ───────────────────────────────────────
function fetchGitHub(path: string): Promise<any> {
    return new Promise((resolve, reject) => {
        const options = {
            hostname: "api.github.com",
            path,
            headers: {
                Authorization: `token ${GITHUB_TOKEN}`,
                "User-Agent": "solo-empire-sync",
                Accept: "application/vnd.github.v3+json",
            },
        };

        https.get(options, (res) => {
            let data = "";
            res.on("data", (chunk) => (data += chunk));
            res.on("end", () => {
                if (res.statusCode !== 200) {
                    reject(new Error(`GitHub API ${res.statusCode}: ${data}`));
                    return;
                }
                try {
                    resolve(JSON.parse(data));
                } catch (e) {
                    reject(e);
                }
            });
        }).on("error", reject);
    });
}

// ─── Main sync ───────────────────────────────────────────────
async function syncRepos() {
    console.log("🔄 Starting GitHub sync...\n");

    // Fetch all repos
    let allRepos: any[] = [];
    let page = 1;
    while (true) {
        const repos = await fetchGitHub(
            `/user/repos?per_page=${PER_PAGE}&page=${page}&sort=updated&affiliation=owner`
        );
        if (!Array.isArray(repos) || repos.length === 0) break;
        allRepos = allRepos.concat(repos);
        console.log(`  Page ${page}: ${repos.length} repos`);
        if (repos.length < PER_PAGE) break;
        page++;
    }
    console.log(`\n📦 Total repos fetched: ${allRepos.length}\n`);

    // Track project repos mapping
    const projectRepoMap = new Map<string, { repo: any; cat: RepoCategory }[]>();

    // Group repos into projects
    for (const repo of allRepos) {
        const cat = categorizeRepo(repo.name);

        // Skip excluded repos
        if (cat.project === "excluded") continue;

        // Track project repos
        if (cat.project) {
            if (!projectRepoMap.has(cat.project)) {
                projectRepoMap.set(cat.project, []);
            }
            projectRepoMap.get(cat.project)!.push({ repo, cat });
        }
    }

    console.log(`✅ Repos grouped: ${allRepos.length} fetched`);

    // Upsert projects from categorized repos
    const projectDefinitions: Record<string, { name: string; description: string; category: string; priority: number; techStack: string[] }> = {
        "localcrm": { name: "LocalCRM", description: "Local-first CRM — frontend + backend", category: "product", priority: 2, techStack: ["TypeScript", "Python"] },
        "book-series": { name: "Book Series", description: "Book trading, reading, and marketing applications", category: "product", priority: 2, techStack: ["TypeScript", "Go"] },
        "101-portfolio-projects": { name: "101 Portfolio Projects", description: "107 portfolio frontend projects — productivity, dev tools, content, business, AI", category: "portfolio", priority: 3, techStack: ["TypeScript"] },
        "ai-projects": { name: "AI Projects", description: "AI/ML experiments — chatbots, prompts, NLP, sentiment analysis", category: "experiment", priority: 1, techStack: ["TypeScript", "Python"] },
        "client-work": { name: "Client Work", description: "Client projects", category: "client", priority: 2, techStack: ["TypeScript", "Python"] },
        "solo-empire": { name: "Solo Empire", description: "Knowledge base & solopreneur operating system — registry and tooling", category: "internal", priority: 1, techStack: ["TypeScript"] },
        "landing-page": { name: "Landing Page", description: "Personal landing page", category: "product", priority: 2, techStack: ["TypeScript"] },
        "media-tracker": { name: "Media Tracker", description: "Media tracking application", category: "product", priority: 3, techStack: ["TypeScript", "Go"] },
        "infrastructure": { name: "Infrastructure", description: "Docker configs, backup systems, dev ops", category: "internal", priority: 3, techStack: ["Shell", "Docker"] },
        "documentation": { name: "Documentation", description: "Mermaid docs, flowcharts, git education", category: "internal", priority: 4, techStack: ["Mermaid"] },
        "research": { name: "Research", description: "OCR, audio processing, AI research experiments", category: "experiment", priority: 3, techStack: ["Python"] },
    };

    // Projects and their repositories live in the projects cell.
    const projectsCell = openCellStore("projects", "owner-cli", { channel: "cli" });
    const knownProjects = new Map((await projectsCell.list("projects")).map((p) => [p.slug as string, p]));
    const knownRepos = await projectsCell.list("project-repos");

    let projectsCreated = 0;
    for (const [slug, def] of Object.entries(projectDefinitions)) {
        const repos = projectRepoMap.get(slug) || [];
        if (repos.length === 0 && slug !== "101-portfolio-projects") continue;

        const existing = knownProjects.get(slug);
        const techStackJson = JSON.stringify(def.techStack);
        let projectId: number;

        if (existing) {
            await projectsCell.update("projects", existing.id, { description: def.description, tech_stack: techStackJson });
            projectId = existing.id;
        } else {
            const created = await projectsCell.create("projects", {
                name: def.name,
                slug,
                description: def.description,
                category: def.category,
                status: def.category === "experiment" ? "planning" : "active",
                priority: def.priority,
                tech_stack: techStackJson,
                repo_url: repos.length > 0 ? repos[0].repo.html_url.replace(/\/[^/]+$/, "") : null,
            });
            projectId = created.id;
            knownProjects.set(slug, created);
            projectsCreated++;
        }

        // Link repos to project (a repo already linked to the project is left alone)
        const linked = new Set(knownRepos.filter((r) => r.project_id === projectId).map((r) => r.repo_name));
        for (const { repo, cat } of repos) {
            if (linked.has(repo.name)) continue;
            const created = await projectsCell.create("project-repos", {
                project_id: projectId,
                repo_name: repo.name,
                repo_url: repo.html_url,
                role: cat.role,
                language: repo.language || null,
                is_primary: repos.indexOf({ repo, cat }) === 0,
            });
            knownRepos.push(created);
            linked.add(repo.name);
        }
    }
    console.log(`✅ Projects: ${projectsCreated} created, ${projectRepoMap.size} groups linked\n`);

    // Summary
    const repoCounts = new Map<number, number>();
    for (const repo of knownRepos) repoCounts.set(repo.project_id, (repoCounts.get(repo.project_id) ?? 0) + 1);
    const summary = sortRows([...knownProjects.values()], ["priority"], ["name"])
        .map((p) => ({ ...p, repo_count: repoCounts.get(p.id) ?? 0 }));

    console.log("📊 Project Summary:");
    console.log("─".repeat(60));
    for (const p of summary) {
        console.log(`  ${p.name} [${p.category}] — ${p.status} — ${p.repo_count} repos — priority:${p.priority}`);
    }

    console.log("\n✅ GitHub sync complete!");
}

syncRepos().catch((err) => {
    console.error("Sync failed:", err);
    process.exit(1);
});
