#!/usr/bin/env tsx
/**
 * Record the owner's self-assessed skill levels in the roles-skills cell
 */

import { mcp } from "../../mcp-modules.js";

const { openCellStore } = await mcp("cell-store");

async function main() {
    // Skills live in the roles-skills cell; progress log entries (progress-log) too.
    const rolesSkills = openCellStore("roles-skills", "owner-cli", { channel: "cli" });
    const skillsBySlug = new Map((await rolesSkills.list("skills")).map((s) => [s.slug, s]));

    // Skills to update based on user's self-assessment
    // Format: [slug, progress_percent, status, level]
    const updates = [
        // Web Dev (proficient - 80%)
        ["backend", 80, "proficient", "L2_capable"],

        // AI/ML (proficient - 75%)
        ["ai-agents", 75, "proficient", "L2_capable"],
        ["rag", 75, "proficient", "L2_capable"],
        ["nlp-llm", 70, "practicing", "L2_capable"],
        ["mlops", 65, "practicing", "L2_capable"],
        ["machine-learning", 60, "practicing", "L2_capable"],

        // DevOps/Infra (capable - 60%)
        ["linux", 60, "practicing", "L2_capable"],
        ["containers", 55, "practicing", "L2_capable"],
        ["cloud", 50, "practicing", "L2_capable"],
        ["devops", 55, "practicing", "L2_capable"],

        // Marketing/Business (capable - 55%)
        ["seo-content", 55, "practicing", "L2_capable"],
        ["business-strategy-skill", 50, "practicing", "L2_capable"],
        ["copywriting", 50, "practicing", "L2_capable"],
        ["consultative-selling", 45, "practicing", "L2_capable"],
        ["email-marketing", 45, "practicing", "L2_capable"],

        // Data (some knowledge)
        ["data-engineering", 40, "practicing", "L2_capable"],
        ["database", 50, "practicing", "L2_capable"],

        // Software Engineering
        ["software-architecture", 55, "practicing", "L2_capable"],
        ["api-design", 60, "practicing", "L2_capable"],
    ];

    console.log("Updating skills...\n");

    for (const [slug, progress, status, level] of updates) {
        const skill = skillsBySlug.get(slug as string);

        if (skill) {
            const oldProgress = skill.progress_percent;
            await rolesSkills.update("skills", skill.id, { progress_percent: progress, status, level });

            // Log progress
            await rolesSkills.create("progress-log", {
                skill_id: skill.id,
                activity_type: "review",
                title: `Self-assessment: ${oldProgress}% → ${progress}%`,
                description: "Updated via self-assessment",
                progress_before: oldProgress,
                progress_after: progress,
                logged_at: new Date().toISOString(),
            });

            console.log(
                `✅ ${skill.name}: ${oldProgress}% → ${progress}% (${status})`,
            );
        } else {
            console.log(`⚠️  Skill not found: ${slug}`);
        }
    }

    console.log("\n✅ Skills updated successfully!");
}

main().catch((err) => {
    console.error("Error:", err);
    process.exit(1);
});
