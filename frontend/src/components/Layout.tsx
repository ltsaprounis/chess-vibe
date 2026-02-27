import { NavLink, Outlet } from 'react-router-dom'

export function Layout(): React.JSX.Element {
  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <nav className="border-b border-gray-700 bg-gray-800">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <span className="text-lg font-bold">Chess Vibe</span>
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              isActive ? 'text-blue-400 font-semibold' : 'text-gray-300 hover:text-white'
            }
          >
            Play
          </NavLink>
          <NavLink
            to="/sprt"
            className={({ isActive }) =>
              isActive ? 'text-blue-400 font-semibold' : 'text-gray-300 hover:text-white'
            }
          >
            SPRT Tests
          </NavLink>
          <NavLink
            to="/games"
            className={({ isActive }) =>
              isActive ? 'text-blue-400 font-semibold' : 'text-gray-300 hover:text-white'
            }
          >
            Game Replay
          </NavLink>
        </div>
      </nav>
      <div className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </div>
    </div>
  )
}
