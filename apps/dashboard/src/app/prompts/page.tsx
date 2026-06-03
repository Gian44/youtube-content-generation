import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { query } from "@/lib/db";

export const dynamic = 'force-dynamic';

const categoryColors: Record<string, string> = {
  topic: "bg-violet-500/20 text-violet-400 border-violet-500/30",
  story: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  hook: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  metadata: "bg-green-500/20 text-green-400 border-green-500/30",
  thumbnail: "bg-pink-500/20 text-pink-400 border-pink-500/30",
  comment: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  policy: "bg-red-500/20 text-red-400 border-red-500/30",
  rewrite: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  title_variation: "bg-purple-500/20 text-purple-400 border-purple-500/30",
};

const mockPrompts = [
  { id: "p-001", name: "topic_selection", category: "topic", version: 1, isActive: true, description: "Select daily topic based on weights and cooldown" },
  { id: "p-002", name: "short_story_generation", category: "story", version: 1, isActive: true, description: "Generate 100-160 word short stories for Shorts" },
  { id: "p-003", name: "long_form_story_generation", category: "story", version: 1, isActive: true, description: "Generate 300-600 word stories for long-form" },
  { id: "p-004", name: "hook_generation", category: "hook", version: 1, isActive: true, description: "Generate attention-grabbing opening hooks" },
  { id: "p-005", name: "metadata_generation", category: "metadata", version: 1, isActive: true, description: "Generate YouTube title, description, tags" },
  { id: "p-006", name: "thumbnail_text_generation", category: "thumbnail", version: 1, isActive: true, description: "Generate thumbnail text overlay" },
  { id: "p-007", name: "pinned_comment_generation", category: "comment", version: 1, isActive: true, description: "Generate engaging pinned comment" },
  { id: "p-008", name: "policy_review", category: "policy", version: 1, isActive: true, description: "Review story for policy violations" },
  { id: "p-009", name: "story_rewrite", category: "rewrite", version: 1, isActive: true, description: "Rewrite flagged content to be compliant" },
  { id: "p-010", name: "title_variation", category: "title_variation", version: 1, isActive: true, description: "Generate title variations for A/B testing" },
];

export default async function PromptsPage() {
  // Query prompt templates stats
  const statsRes = await query(`
    SELECT 
      COUNT(*) as total,
      SUM(CASE WHEN is_active = 1 OR is_active = true THEN 1 ELSE 0 END) as active,
      COUNT(DISTINCT category) as categories
    FROM prompt_templates
  `);

  const total = statsRes[0]?.total || 0;
  const active = statsRes[0]?.active || 0;
  const categories = statsRes[0]?.categories || 0;

  const isDemoMode = total === 0;

  // Query templates list
  const dbTemplates = await query(`
    SELECT 
      id,
      name,
      description,
      template,
      category,
      version,
      is_active
    FROM prompt_templates
    ORDER BY name ASC
  `);

  const promptsList = isDemoMode ? mockPrompts : dbTemplates.map((p: any) => ({
    id: p.id,
    name: p.name,
    category: p.category,
    version: Number(p.version),
    isActive: Boolean(p.isActive),
    description: p.description || "n/a",
  }));

  const displayTotal = isDemoMode ? 10 : total;
  const displayActive = isDemoMode ? 10 : active;
  const displayCategories = isDemoMode ? 9 : categories;

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold gradient-text">Prompt Lab</h1>
            <p className="mt-1 text-muted-foreground">Manage and test prompt templates for content generation</p>
          </div>
          {isDemoMode && (
            <Badge variant="outline" className="bg-yellow-500/20 text-yellow-400 border-yellow-500/30">
              💡 Demo Mode: Mock Data Shown
            </Badge>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          {[
            { label: "Total Prompts", value: String(displayTotal), icon: "📝" },
            { label: "Active", value: String(displayActive), icon: "✅" },
            { label: "Categories", value: String(displayCategories), icon: "🏷️" },
          ].map((s) => (
            <Card key={s.label} className="border-border/50 bg-card/50 backdrop-blur-sm">
              <CardContent className="flex items-center gap-3 p-4">
                <span className="text-xl">{s.icon}</span>
                <div>
                  <p className="text-xl font-bold">{s.value}</p>
                  <p className="text-xs text-muted-foreground">{s.label}</p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        <div className="space-y-3">
          {promptsList.length > 0 ? (
            promptsList.map((prompt) => (
              <Card key={prompt.id} className="border-border/30 bg-card/30 backdrop-blur-sm transition-all hover:border-primary/20 cursor-pointer">
                <CardContent className="p-5">
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex-1">
                      <div className="flex items-center gap-3 mb-1">
                        <h3 className="font-medium text-sm font-mono">{prompt.name}</h3>
                        <Badge variant="outline" className={`text-[10px] ${categoryColors[prompt.category] || "bg-gray-500/20 text-gray-400"}`}>
                          {prompt.category}
                        </Badge>
                        <Badge variant="outline" className="text-[10px]">v{prompt.version}</Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">{prompt.description}</p>
                    </div>
                    <div className="shrink-0 text-right text-xs text-muted-foreground">
                      <Badge variant="outline" className={`mt-1 text-[10px] ${prompt.isActive ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" : "bg-red-500/20 text-red-400 border-red-500/30"}`}>
                        {prompt.isActive ? "Active" : "Inactive"}
                      </Badge>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))
          ) : (
            <div className="text-center py-6 text-muted-foreground text-sm">
              No prompt templates found.
            </div>
          )}
        </div>

        {/* Prompt Testing Area */}
        <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
          <CardHeader>
            <CardTitle className="text-lg">🧪 Test Prompt</CardTitle>
            <CardDescription>Select a prompt template and test it with sample variables</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg border border-dashed border-border/50 bg-muted/20 p-8 text-center">
              <p className="text-muted-foreground text-sm">Select a prompt template above to test it</p>
              <p className="text-xs text-muted-foreground mt-2">Templates can be edited and tested with different variables</p>
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
