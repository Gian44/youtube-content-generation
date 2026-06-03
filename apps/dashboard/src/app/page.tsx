import { DashboardLayout } from "@/components/dashboard-layout";
import { DashboardContent } from "@/components/dashboard-content";
import { getFullDashboardData } from "@/lib/dashboard-data";

export const dynamic = 'force-dynamic';

export default async function OverviewPage() {
  const initialData = await getFullDashboardData({ force: true });

  return (
    <DashboardLayout>
      <DashboardContent initialData={initialData} />
    </DashboardLayout>
  );
}
