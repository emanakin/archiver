import "./globals.css";
import type { Metadata } from "next";
import { Dashboard } from "@/features/dashboard/dashboard";
export const metadata:Metadata={title:"Archive Desk",description:"Private archive operations"};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="en"><body><Dashboard />{children}</body></html>;}
