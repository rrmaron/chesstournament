window.CoachBoard = (function () {
  var PIECES = "https://chessboardjs.com/img/chesspieces/wikipedia/{piece}.png";

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function bindNav(boardId, handler) {
    document.querySelectorAll("[data-board-nav='" + boardId + "']").forEach(function (btn) {
      btn.addEventListener("click", function () { handler(btn.dataset.action); });
    });
  }

  // Study viewer: step through a PGN, or just show a position.
  function viewer(opts) {
    var startFen = opts.fen || "";
    var moves = [];
    if (opts.pgn) {
      var parsed = new Chess();
      if (parsed.load_pgn(opts.pgn, { sloppy: true })) {
        moves = parsed.history();
        var header = parsed.header();
        if (header && header.FEN) startFen = header.FEN;
      }
    }
    var game = new Chess();
    if (startFen) game.load(startFen);
    var baseFen = game.fen();
    var ply = 0;
    var board = Chessboard(opts.boardId, { position: baseFen, pieceTheme: PIECES });

    function render() {
      game.load(baseFen);
      for (var i = 0; i < ply; i++) game.move(moves[i], { sloppy: true });
      board.position(game.fen());
      var list = document.getElementById(opts.movesId);
      if (!list) return;
      var html = "";
      for (var j = 0; j < moves.length; j++) {
        if (j % 2 === 0) html += "<span class='text-muted'>" + (Math.floor(j / 2) + 1) + ".</span> ";
        html += "<a href='#' data-ply='" + (j + 1) + "' class='text-decoration-none me-1 " +
          (j + 1 === ply ? "fw-bold text-primary" : "text-body") + "'>" + moves[j] + "</a> ";
      }
      list.innerHTML = html || "<span class='text-muted'>Position only - no moves to play through.</span>";
      list.querySelectorAll("a[data-ply]").forEach(function (a) {
        a.addEventListener("click", function (e) {
          e.preventDefault();
          ply = parseInt(a.dataset.ply, 10);
          render();
        });
      });
    }

    function go(n) {
      ply = Math.max(0, Math.min(moves.length, n));
      render();
    }

    bindNav(opts.boardId, function (action) {
      if (action === "start") go(0);
      else if (action === "prev") go(ply - 1);
      else if (action === "next") go(ply + 1);
      else if (action === "end") go(moves.length);
      else if (action === "flip") board.flip();
    });
    render();
  }

  // Puzzle: the student plays; every move is checked by the server, which
  // answers with the opponent's reply so the solution never ships to the browser.
  function puzzle(opts) {
    var game = new Chess(opts.fen);
    var ply = 0, solved = false, busy = false;
    var board = Chessboard(opts.boardId, {
      draggable: true,
      position: opts.fen,
      pieceTheme: PIECES,
      orientation: game.turn() === "w" ? "white" : "black",
      onDragStart: function (source, piece) {
        return !solved && !busy && piece.charAt(0) === game.turn();
      },
      onDrop: function (source, target) {
        var move = game.move({ from: source, to: target, promotion: "q" });
        if (move === null) return "snapback";
        busy = true;
        fetch("/api/coaching/puzzle-move", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hid: opts.hid, ply: ply, san: move.san })
        }).then(function (r) { return r.json(); }).then(function (d) {
          if (!d.correct) {
            game.undo();
            board.position(game.fen());
            setText(opts.statusId, "Not quite - try again.");
            return;
          }
          ply += 1;
          if (d.reply) {
            game.move(d.reply, { sloppy: true });
            ply += 1;
            board.position(game.fen());
          }
          if (d.solved) {
            solved = true;
            setText(opts.statusId, "Solved! Well done.");
            var el = document.getElementById(opts.statusId);
            if (el) el.className = "mt-3 mb-0 text-center fw-semibold text-success";
          } else {
            setText(opts.statusId, "Correct - keep going.");
          }
        }).catch(function () {
          game.undo();
          board.position(game.fen());
          setText(opts.statusId, "Could not check that move - try again.");
        }).finally(function () { busy = false; });
      },
      onSnapEnd: function () { board.position(game.fen()); }
    });
    setText(opts.statusId, (game.turn() === "w" ? "White" : "Black") + " to move - drag a piece to play.");
    bindNav(opts.boardId, function (action) { if (action === "flip") board.flip(); });
  }

  return { viewer: viewer, puzzle: puzzle };
})();
