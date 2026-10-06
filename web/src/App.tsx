import { BrowserRouter, Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { ChatPage } from './pages/Chat'
import { HistoryList, TranscriptPage } from './pages/History'
import { Home } from './pages/Home'
import { NewConversation } from './pages/NewConversation'
import { ProgressPage } from './pages/ProgressPage'
import { Books } from './pages/Books'
import { ReadingPage } from './pages/ReadingPage'
import { Songs } from './pages/Songs'
import { WhatNext } from './pages/WhatNext'
import { ConversationsProvider } from './state/conversations'

export default function App() {
  return (
    <ConversationsProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="new" element={<NewConversation />} />
            <Route path="chat/:sessionId" element={<ChatPage />} />
            <Route path="history" element={<HistoryList />} />
            <Route path="history/:sessionId" element={<TranscriptPage />} />
            <Route path="progress" element={<ProgressPage />} />
            <Route path="next" element={<WhatNext />} />
            <Route path="reading/:sessionId" element={<ReadingPage />} />
            <Route path="books" element={<Books />} />
            <Route path="songs" element={<Songs />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConversationsProvider>
  )
}
