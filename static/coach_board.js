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

  // Position editor for coaches: spare pieces to drag on, drag off the board to
  // remove, record a puzzle solution by playing it, or load a position from an image.
  function editor(opts) {
    var fenInput = document.getElementById(opts.fenInputId);
    var sideSel = document.getElementById(opts.sideId);
    var solutionInput = document.getElementById(opts.solutionInputId);
    var recordBtn = document.getElementById(opts.recordBtnId);
    var board = null, game = null, recording = false;

    function expand(row) {
      var out = "";
      for (var i = 0; i < row.length; i++) {
        var c = row.charAt(i);
        out += (c >= "1" && c <= "8") ? new Array(parseInt(c, 10) + 1).join(" ") : c;
      }
      return out;
    }
    function castling(placement) {
      var rows = placement.split("/");
      var r1 = expand(rows[7] || ""), r8 = expand(rows[0] || "");
      var s = "";
      if (r1.charAt(4) === "K") { if (r1.charAt(7) === "R") s += "K"; if (r1.charAt(0) === "R") s += "Q"; }
      if (r8.charAt(4) === "k") { if (r8.charAt(7) === "r") s += "k"; if (r8.charAt(0) === "r") s += "q"; }
      return s || "-";
    }
    function placementOf(fen) { return (fen || "").trim().split(/\s+/)[0]; }
    function currentFen() {
      var placement = board.fen();
      return placement + " " + (sideSel ? sideSel.value : "w") + " " + castling(placement) + " - 0 1";
    }
    function syncInput() { if (fenInput && !recording) fenInput.value = currentFen(); }

    function buildEditor(position, silent) {
      if (board) board.destroy();
      board = Chessboard(opts.boardId, {
        draggable: true, sparePieces: true, dropOffBoard: "trash",
        position: position, pieceTheme: PIECES,
        onChange: function () { setTimeout(syncInput, 0); }
      });
      if (!silent) syncInput();
    }

    function loadFen(fen) {
      var placement = placementOf(fen);
      if (!placement) return;
      var side = fen.trim().split(/\s+/)[1];
      if (sideSel && (side === "w" || side === "b")) sideSel.value = side;
      if (recording) stopRecording();
      buildEditor(placement);
    }

    function startRecording() {
      var fen = currentFen();
      game = new Chess();
      if (!game.load(fen)) {
        setText(opts.statusId, "That position isn't legal yet - check both kings are on the board and the side to move.");
        return;
      }
      recording = true;
      if (fenInput) fenInput.value = fen;
      board.destroy();
      board = Chessboard(opts.boardId, {
        draggable: true, position: placementOf(fen), pieceTheme: PIECES,
        onDragStart: function (source, piece) { return piece.charAt(0) === game.turn(); },
        onDrop: function (source, target) {
          var move = game.move({ from: source, to: target, promotion: "q" });
          if (move === null) return "snapback";
          if (solutionInput) solutionInput.value = (solutionInput.value + " " + move.san).trim();
          setText(opts.statusId, "Recorded " + move.san + ". Play both sides' moves, then click Done.");
        },
        onSnapEnd: function () { board.position(game.fen()); }
      });
      if (recordBtn) recordBtn.textContent = "Done";
      setText(opts.statusId, "Recording: play the solution on the board, both sides. Click Done when finished.");
    }
    function stopRecording() {
      recording = false;
      if (recordBtn) recordBtn.textContent = "Record on board";
      buildEditor(placementOf(fenInput ? fenInput.value : "") || "start", true);
      setText(opts.statusId, "");
    }
    function undoMove() {
      if (!recording || !game || !game.undo()) return;
      board.position(game.fen());
      if (solutionInput) {
        var moves = solutionInput.value.trim().split(/\s+/);
        moves.pop();
        solutionInput.value = moves.join(" ");
      }
    }

    function sendImage(file) {
      if (!file || file.type.indexOf("image/") !== 0) return;
      setText(opts.statusId, "Reading the board from your image…");
      var body = new FormData();
      body.append("image", file, file.name || "board.png");
      fetch("/api/coaching/board-from-image", { method: "POST", body: body })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) { setText(opts.statusId, res.data.detail || "Could not read that image."); return; }
          loadFen(res.data.fen);
          var usage = res.data.usage && res.data.usage.summary ? " API usage: " + res.data.usage.summary + "." : "";
          setText(opts.statusId, "Position loaded from the image - check every piece and set the side to move before assigning." + usage);
        })
        .catch(function () { setText(opts.statusId, "Could not read that image."); });
    }

    var fileInput = document.getElementById(opts.imageInputId);
    if (fileInput) fileInput.addEventListener("change", function () { sendImage(fileInput.files[0]); fileInput.value = ""; });
    var drop = document.getElementById(opts.dropId);
    if (drop) {
      drop.addEventListener("dragover", function (e) { e.preventDefault(); drop.classList.add("border-primary"); });
      drop.addEventListener("dragleave", function () { drop.classList.remove("border-primary"); });
      drop.addEventListener("drop", function (e) {
        e.preventDefault();
        drop.classList.remove("border-primary");
        sendImage(e.dataTransfer.files[0]);
      });
    }
    document.addEventListener("paste", function (e) {
      var files = (e.clipboardData && e.clipboardData.files) || [];
      for (var i = 0; i < files.length; i++) {
        if (files[i].type.indexOf("image/") === 0) { e.preventDefault(); sendImage(files[i]); return; }
      }
    });
    if (sideSel) sideSel.addEventListener("change", syncInput);

    bindNav(opts.boardId, function (action) {
      if (recording && action !== "record" && action !== "undo" && action !== "flip") stopRecording();
      if (action === "start") { board.start(); syncInput(); }
      else if (action === "clear") { board.clear(); syncInput(); }
      else if (action === "flip") board.flip();
      else if (action === "load") loadFen(fenInput ? fenInput.value : "");
      else if (action === "record") { if (recording) stopRecording(); else startRecording(); }
      else if (action === "undo") undoMove();
    });

    buildEditor(placementOf(fenInput ? fenInput.value : "") || "start", true);
  }

  return { viewer: viewer, puzzle: puzzle, editor: editor };
})();
