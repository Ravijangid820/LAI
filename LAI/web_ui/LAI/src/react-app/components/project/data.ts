import { Project } from "./types";

export const INITIAL_PROJECTS: Project[] = [
  {
    id: "1",
    name: "Windtech Farm Phase 1",
    description: "Legal due diligence for wind farm development in North Germany",
    instructions:
      "This is a production grade project so there should be no mistakes and loopholes in maintaining the code base.",
    status: "active",
    owner: "You",
    createdDate: "2024-01-15",
    files: [
      { id: "f1", name: "environmental_assessment.pdf", size: 5.4, uploadDate: "2024-02-18", type: "PDF", lines: 312 },
      { id: "f2", name: "grid_connection_agreement.docx", size: 2.1, uploadDate: "2024-02-17", type: "DOCX", lines: 87 },
      { id: "f3", name: "compliance_checklist.xlsx", size: 1.2, uploadDate: "2024-01-10", type: "XLSX", lines: 215 },
      { id: "f4", name: "risk_mitigation.docx", size: 2.8, uploadDate: "2024-01-10", type: "DOCX", lines: 124 },
      { id: "f5", name: "site_survey.pdf", size: 3.2, uploadDate: "2024-02-15", type: "PDF", lines: 291 },
      { id: "f6", name: "final_report.pdf", size: 8.7, uploadDate: "2024-01-10", type: "PDF", lines: 513 },
    ],
    teamMembers: 3,
    conversations: [
      {
        id: "conv1",
        title: "Environmental compliance requirements",
        lastMessage: "The project must comply with EU Directive 2011/92/EU...",
        timestamp: "2 hours ago",
        messages: [
          { id: "m1", message: "What are the environmental compliance requirements?", sender: "user", timestamp: "10:30" },
          {
            id: "m2",
            message:
              "The project must comply with EU Directive 2011/92/EU on Environmental Impact Assessment. Key requirements include a full EIA report, public consultation, and authority approval.",
            sender: "assistant",
            timestamp: "10:31",
          },
        ],
      },
      {
        id: "conv2",
        title: "LAI project contract analysis scope confirmation",
        lastMessage: "Grid connection procedures typically involve TSO...",
        timestamp: "1 day ago",
        messages: [
          { id: "m3", message: "What about grid connection procedures?", sender: "user", timestamp: "11:15" },
          {
            id: "m4",
            message:
              "Grid connection procedures typically involve TSO (Transmission System Operator) approval. The process includes technical review, feasibility study, and connection agreement signing.",
            sender: "assistant",
            timestamp: "11:16",
          },
        ],
      },
      {
        id: "conv3",
        title: "Expert datasets classification and SFT format conversion progress",
        lastMessage: "Risk assessment covering weather-related risks...",
        timestamp: "6 days ago",
        messages: [
          { id: "m5", message: "Risk assessment for solar project", sender: "user", timestamp: "09:00" },
          {
            id: "m6",
            message:
              "Solar projects present several risks: weather-related, technical, regulatory, and financial. Let me analyze each category in detail.",
            sender: "assistant",
            timestamp: "09:02",
          },
        ],
      },
      {
        id: "conv4",
        title: "LAI implementation roadmap analysis and next steps",
        lastMessage: "Roadmap has been updated with new milestones...",
        timestamp: "6 days ago",
        messages: [
          { id: "m7", message: "Can you help with the implementation roadmap?", sender: "user", timestamp: "14:00" },
          {
            id: "m8",
            message:
              "The implementation roadmap should cover three phases: planning, execution, and review. I'll outline the key milestones for each.",
            sender: "assistant",
            timestamp: "14:01",
          },
        ],
      },
      {
        id: "conv5",
        title: "Data deduplication and field separation",
        lastMessage: "Deduplication logic applied across all datasets...",
        timestamp: "8 days ago",
        messages: [
          { id: "m9", message: "We need to deduplicate the datasets", sender: "user", timestamp: "16:00" },
          {
            id: "m10",
            message:
              "I can help with that. Deduplication logic can be applied across all datasets using a combination of field matching and fuzzy logic.",
            sender: "assistant",
            timestamp: "16:01",
          },
        ],
      },
    ],
  },
  {
    id: "2",
    name: "Renewable Energy Site B",
    description: "Risk assessment and compliance review for solar installation",
    instructions: "Focus on regulatory compliance and risk mitigation strategies.",
    status: "active",
    owner: "You",
    createdDate: "2024-02-01",
    files: [
      { id: "f7", name: "site_survey.pdf", size: 3.2, uploadDate: "2024-02-15", type: "PDF", lines: 178 },
    ],
    teamMembers: 2,
    conversations: [
      {
        id: "conv6",
        title: "Risk assessment for solar project",
        lastMessage: "Solar projects present several risks...",
        timestamp: "15 days ago",
        messages: [
          { id: "m11", message: "Risk assessment for solar project", sender: "user", timestamp: "09:00" },
          {
            id: "m12",
            message: "Solar projects present several risks: weather-related, technical, regulatory, and financial.",
            sender: "assistant",
            timestamp: "09:02",
          },
        ],
      },
    ],
  },
  {
    id: "3",
    name: "Nordic Wind Project",
    description: "Comprehensive legal analysis for international wind energy project",
    instructions: "Ensure all international regulations are reviewed and documented.",
    status: "completed",
    owner: "You",
    createdDate: "2023-11-20",
    files: [
      { id: "f8", name: "final_report.pdf", size: 8.7, uploadDate: "2024-01-10", type: "PDF", lines: 402 },
      { id: "f9", name: "compliance_checklist.xlsx", size: 1.2, uploadDate: "2024-01-10", type: "XLSX", lines: 98 },
      { id: "f10", name: "risk_mitigation.docx", size: 2.8, uploadDate: "2024-01-10", type: "DOCX", lines: 145 },
    ],
    teamMembers: 5,
    conversations: [
      {
        id: "conv7",
        title: "Project completed successfully",
        lastMessage: "All legal requirements have been met...",
        timestamp: "43 days ago",
        messages: [
          { id: "m13", message: "Project completed successfully", sender: "user", timestamp: "15:30" },
          {
            id: "m14",
            message:
              "Congratulations! All legal requirements have been met. Final documentation is ready for submission.",
            sender: "assistant",
            timestamp: "15:31",
          },
        ],
      },
    ],
  },
];